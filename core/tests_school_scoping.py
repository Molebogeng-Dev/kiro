"""Cross-school isolation for the six views retrofitted in Sprint 8b.

Every test here follows one shape, the ownership-boundary pattern from Sprints
3/4: build two schools, act as someone at school A, and prove they see school A's
data and never school B's — a same-school case that succeeds and a cross-school
case that returns nothing or 404s. The ``None``-school guard (a user with no
school sees no one, not everyone else with no school) gets its own test, since
that is the subtle failure mode the retrofit had to avoid.
"""

import json

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from attendance.models import Attendance, FaceEnrollment
from classroom.models import Assignment, StudyMaterial
from core.models import School
from marking.models import Memorandum
from marking.tests.support import make_upload

PASSWORD = "scoping-tests-passphrase-8b"


def make_school(name, *, min_grade=1, max_grade=12):
    return School.objects.create(name=name, min_grade=min_grade, max_grade=max_grade)


def make_teacher(username, school):
    return User.objects.create_user(
        username=username, email=f"{username}@example.com", password=PASSWORD,
        role=Role.TEACHER, school=school,
    )


def make_student(username, school, grade=5):
    return User.objects.create_user(
        username=username, email=f"{username}@example.com", password=PASSWORD,
        role=Role.STUDENT, grade=grade, school=school,
    )


class TwoSchools(TestCase):
    """Shared fixture: schools A and B, each with a teacher."""

    @classmethod
    def setUpTestData(cls):
        cls.school_a = make_school("Alpha School")
        cls.school_b = make_school("Beta School")
        cls.teacher_a = make_teacher("teacher-a", cls.school_a)
        cls.teacher_b = make_teacher("teacher-b", cls.school_b)


class RollCallScopingTests(TwoSchools):
    def test_roll_call_lists_only_this_schools_primary_learners(self):
        mine = make_student("primary-a", self.school_a, grade=3)
        theirs = make_student("primary-b", self.school_b, grade=3)

        self.client.force_login(self.teacher_a)
        response = self.client.get(reverse("attendance:roll_call"))

        self.assertContains(response, "primary-a")
        self.assertNotContains(response, "primary-b")

    def test_a_tampered_cross_school_id_is_not_marked_present(self):
        theirs = make_student("primary-b", self.school_b, grade=3)

        self.client.force_login(self.teacher_a)
        self.client.post(reverse("attendance:roll_call"), {"present": [str(theirs.id)]})

        # The view filters submitted ids against this school's primary list, so
        # the cross-school id does nothing.
        self.assertFalse(Attendance.objects.filter(student=theirs).exists())


class EnrollmentAndCheckInScopingTests(TwoSchools):
    def test_enrollment_offers_only_this_schools_secondary_learners(self):
        make_student("secondary-a", self.school_a, grade=9)
        make_student("secondary-b", self.school_b, grade=9)

        self.client.force_login(self.teacher_a)
        response = self.client.get(reverse("attendance:enroll"))

        self.assertContains(response, "secondary-a")
        self.assertNotContains(response, "secondary-b")

    def test_a_face_enrolled_at_another_school_is_never_matched(self):
        theirs = make_student("secondary-b", self.school_b, grade=9)
        FaceEnrollment.objects.create(
            student=theirs,
            descriptor=[0.0] * 128,
            enrolled_by=self.teacher_b,
            consent_confirmed=True,
        )

        self.client.force_login(self.teacher_a)
        self.client.post(
            reverse("attendance:check_in"),
            {"descriptor": json.dumps([0.0] * 128)},  # would match theirs, if visible
        )

        # School A's check-in only matches School A's enrolled faces, so the
        # other school's student is never checked in here.
        self.assertFalse(Attendance.objects.filter(student=theirs).exists())


class AttendanceHistoryScopingTests(TwoSchools):
    def test_history_shows_only_this_schools_present_learners(self):
        mine = make_student("present-a", self.school_a, grade=4)
        theirs = make_student("present-b", self.school_b, grade=4)
        for learner in (mine, theirs):
            Attendance.objects.create(
                student=learner, date=timezone.localdate(),
                method=Attendance.Method.MANUAL, arrived_at=timezone.now(),
            )

        self.client.force_login(self.teacher_a)
        response = self.client.get(reverse("attendance:history"))

        self.assertContains(response, "present-a")
        self.assertNotContains(response, "present-b")


class ProgressScopingTests(TwoSchools):
    def test_the_list_shows_only_this_schools_learners(self):
        make_student("progress-a", self.school_a)
        make_student("progress-b", self.school_b)

        self.client.force_login(self.teacher_a)
        response = self.client.get(reverse("core:progress_dashboard"))

        self.assertContains(response, "progress-a")
        self.assertNotContains(response, "progress-b")

    def test_a_cross_school_rollup_is_a_404(self):
        theirs = make_student("progress-b", self.school_b)

        self.client.force_login(self.teacher_a)
        response = self.client.get(
            reverse("core:progress_student", args=[theirs.id])
        )
        self.assertEqual(response.status_code, 404)

    def test_a_same_school_rollup_succeeds(self):
        mine = make_student("progress-a", self.school_a)

        self.client.force_login(self.teacher_a)
        response = self.client.get(
            reverse("core:progress_student", args=[mine.id])
        )
        self.assertEqual(response.status_code, 200)


class MarkingPickerScopingTests(TwoSchools):
    def test_the_picker_lists_only_this_schools_learners(self):
        make_student("markable-a", self.school_a)
        make_student("markable-b", self.school_b)

        self.client.force_login(self.teacher_a)
        response = self.client.get(reverse("marking:mark_paper"))

        self.assertContains(response, "markable-a")
        self.assertNotContains(response, "markable-b")

    def test_a_cross_school_learner_cannot_be_marked(self):
        theirs = make_student("markable-b", self.school_b)
        memo = Memorandum.objects.create(
            title="A memo", content="Q1 (2).", created_by=self.teacher_a
        )

        self.client.force_login(self.teacher_a)
        response = self.client.post(
            reverse("marking:mark_paper"),
            {
                "student": str(theirs.id),  # another school's learner
                "memorandum": str(memo.id),
                "image": make_upload(),
            },
        )

        # The ModelChoiceField validates the id against this school's learners,
        # so the cross-school id is rejected and no paper is created.
        self.assertIn("student", response.context["form"].errors)
        from marking.models import Paper

        self.assertFalse(Paper.objects.filter(student=theirs).exists())


class ClassroomScopingTests(TwoSchools):
    def setUp(self):
        self.material_a = StudyMaterial.objects.create(
            title="Material Alpha", content="From school A.", created_by=self.teacher_a
        )
        self.material_b = StudyMaterial.objects.create(
            title="Material Beta", content="From school B.", created_by=self.teacher_b
        )
        memo_a = Memorandum.objects.create(
            title="Memo A", content="Q1 (2).", created_by=self.teacher_a
        )
        memo_b = Memorandum.objects.create(
            title="Memo B", content="Q1 (2).", created_by=self.teacher_b
        )
        self.assignment_a = Assignment.objects.create(
            title="Assignment Alpha", instructions="Do it.",
            memorandum=memo_a, created_by=self.teacher_a,
        )
        self.assignment_b = Assignment.objects.create(
            title="Assignment Beta", instructions="Do it.",
            memorandum=memo_b, created_by=self.teacher_b,
        )
        self.student_a = make_student("classroom-a", self.school_a)
        self.client.force_login(self.student_a)

    def test_materials_show_only_this_schools_items(self):
        response = self.client.get(reverse("classroom:student_material_list"))
        self.assertContains(response, "Material Alpha")
        self.assertNotContains(response, "Material Beta")

    def test_assignments_show_only_this_schools_items(self):
        response = self.client.get(reverse("classroom:student_assignment_list"))
        self.assertContains(response, "Assignment Alpha")
        self.assertNotContains(response, "Assignment Beta")

    def test_a_cross_school_material_detail_is_a_404(self):
        response = self.client.get(
            reverse("classroom:student_material_detail", args=[self.material_b.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_submitting_to_a_cross_school_assignment_is_a_404(self):
        response = self.client.get(
            reverse("classroom:submit_homework", args=[self.assignment_b.pk])
        )
        self.assertEqual(response.status_code, 404)


class NoSchoolGuardTests(TestCase):
    """The flagged subtlety: school=None must scope to nothing, not to every
    other school-less row."""

    def test_a_teacher_with_no_school_sees_no_learners(self):
        # A pre-Sprint-8a teacher and student, both with school=None.
        teacher = User.objects.create_user(
            username="no-school-teacher", email="nst@example.com",
            password=PASSWORD, role=Role.TEACHER,
        )
        User.objects.create_user(
            username="no-school-student", email="nss@example.com",
            password=PASSWORD, role=Role.STUDENT, grade=3,
        )

        self.client.force_login(teacher)

        roll_call = self.client.get(reverse("attendance:roll_call"))
        self.assertNotContains(roll_call, "no-school-student")

        progress = self.client.get(reverse("core:progress_dashboard"))
        self.assertNotContains(progress, "no-school-student")


class RegistrationGradeRangeTests(TestCase):
    """Student registration rejects a grade outside the school's range (8b)."""

    def setUp(self):
        self.secondary = make_school("Secondary Only", min_grade=8, max_grade=12)

    def _register(self, grade):
        return self.client.post(
            reverse("accounts:register"),
            {
                "username": "range-student",
                "first_name": "R", "last_name": "S",
                "email": "range@example.com",
                "role": Role.STUDENT.value,
                "grade": str(grade),
                "school": self.secondary.id,
                "code": self.secondary.student_join_code,
                "password1": PASSWORD, "password2": PASSWORD,
            },
        )

    def test_a_grade_below_the_schools_range_is_rejected(self):
        response = self._register(3)
        self.assertIn("grade", response.context["form"].errors)
        self.assertFalse(User.objects.filter(username="range-student").exists())

    def test_a_grade_within_the_schools_range_is_accepted(self):
        self._register(9)
        student = User.objects.get(username="range-student")
        self.assertEqual(student.grade, 9)
        self.assertEqual(student.school, self.secondary)
