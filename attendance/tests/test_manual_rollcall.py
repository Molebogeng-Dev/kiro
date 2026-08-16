"""Manual roll-call for primary learners, plus the shared model rules."""

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from attendance.models import Attendance
from attendance.services import mark_present_manually

from .support import make_parent, make_student, make_teacher


class RollCallTests(TestCase):
    def setUp(self):
        self.teacher = make_teacher("rc-teacher")
        self.p1 = make_student("primary-one", grade=3)
        self.p2 = make_student("primary-two", grade=6)
        self.secondary = make_student("secondary-one", grade=9)
        self.client.force_login(self.teacher)
        self.url = reverse("attendance:roll_call")

    def test_the_list_shows_primary_learners_only(self):
        response = self.client.get(self.url)

        self.assertContains(response, "primary-one")
        self.assertContains(response, "primary-two")
        # A secondary learner must not appear in the roll-call list.
        self.assertNotContains(response, "secondary-one")

    def test_ticking_learners_marks_them_present(self):
        self.client.post(self.url, {"present": [self.p1.id, self.p2.id]})

        for student in (self.p1, self.p2):
            record = Attendance.objects.get(student=student, date=timezone.localdate())
            self.assertEqual(record.method, Attendance.Method.MANUAL)
            self.assertIsNotNone(record.arrived_at)
            self.assertIsNone(record.departed_at)

    def test_only_one_record_per_student_per_day(self):
        self.client.post(self.url, {"present": [self.p1.id]})
        self.client.post(self.url, {"present": [self.p1.id]})

        self.assertEqual(
            Attendance.objects.filter(student=self.p1, date=timezone.localdate()).count(),
            1,
        )

    def test_a_secondary_id_in_the_post_is_ignored(self):
        """Routing enforced server-side: a tampered secondary id does nothing."""
        self.client.post(self.url, {"present": [self.secondary.id]})

        self.assertFalse(Attendance.objects.filter(student=self.secondary).exists())

    def test_unticking_does_not_remove_an_existing_record(self):
        self.client.post(self.url, {"present": [self.p1.id]})
        # A later submission that omits p1 must not delete their presence.
        self.client.post(self.url, {"present": [self.p2.id]})

        self.assertTrue(
            Attendance.objects.filter(student=self.p1, date=timezone.localdate()).exists()
        )

    def test_present_learners_show_as_already_marked(self):
        self.client.post(self.url, {"present": [self.p1.id]})
        response = self.client.get(self.url)
        self.assertEqual(response.context["present_count"], 1)


class MultiTeacherAccessTests(TestCase):
    """Attendance is a shared function: it is not scoped to one teacher."""

    def setUp(self):
        self.teacher_a = make_teacher("teacher-a")
        self.teacher_b = make_teacher("teacher-b")
        self.student = make_student("shared-kid", grade=5)

    def test_one_teacher_marks_and_another_sees_it(self):
        self.client.force_login(self.teacher_a)
        self.client.post(reverse("attendance:roll_call"), {"present": [self.student.id]})

        # A different teacher sees the same record in today's list.
        self.client.force_login(self.teacher_b)
        response = self.client.get(reverse("attendance:history"))
        self.assertContains(response, "shared-kid")

    def test_another_teacher_can_also_mark(self):
        self.client.force_login(self.teacher_b)
        response = self.client.post(
            reverse("attendance:roll_call"), {"present": [self.student.id]}
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Attendance.objects.filter(student=self.student).exists())


class HistoryTests(TestCase):
    def setUp(self):
        self.teacher = make_teacher("hist-teacher")
        self.client.force_login(self.teacher)

    def test_history_shows_todays_present_learners_with_method(self):
        student = make_student("hist-kid", grade=2)
        mark_present_manually(student)

        response = self.client.get(reverse("attendance:history"))
        self.assertContains(response, "hist-kid")
        self.assertContains(response, "Roll-call")

    def test_empty_history_is_friendly(self):
        response = self.client.get(reverse("attendance:history"))
        self.assertContains(response, "No one marked present yet")


class AttendanceModelTests(TestCase):
    def test_one_record_per_student_per_day_is_enforced_by_the_database(self):
        student = make_student("dbcon-kid", grade=4)
        Attendance.objects.create(
            student=student, method=Attendance.Method.MANUAL, arrived_at=timezone.now()
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            Attendance.objects.create(
                student=student, method=Attendance.Method.FACIAL, arrived_at=timezone.now()
            )

    def test_attendance_must_belong_to_a_student(self):
        from django.core.exceptions import ValidationError

        teacher = make_teacher("not-a-student")
        record = Attendance(student=teacher, method=Attendance.Method.MANUAL)
        with self.assertRaises(ValidationError):
            record.full_clean()

    def test_marking_present_twice_keeps_the_first_arrival(self):
        student = make_student("idem-kid", grade=1)
        first, created_first = mark_present_manually(student)
        second, created_second = mark_present_manually(student)

        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.arrived_at, second.arrived_at)
