"""The grade addition to student registration (Sprint 5)."""

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from core.models import School, TeacherInvite

PASSWORD = "an-oddly-specific-passphrase-42"


class GradeAtRegistrationTests(TestCase):
    def setUp(self):
        # Registration now joins a school with a code (Sprint 8a); supply valid
        # ones so these tests still isolate the grade behaviour they check.
        self.school = School.objects.create(
            name="Attendance Test School", min_grade=1, max_grade=12
        )

    def _code_for(self, role):
        if role == "teacher":
            return TeacherInvite.create_for(
                school=self.school, teacher_name="T", assigned_grades="8"
            ).code
        if role == "parent":
            return self.school.parent_student_join_code
        return self.school.student_join_code

    def register(self, username, role, **extra):
        payload = {
            "username": username,
            "first_name": "Test",
            "last_name": "Person",
            "email": f"{username}@example.com",
            "role": role,
            "school": self.school.id,
            "code": self._code_for(role),
            "password1": PASSWORD,
            "password2": PASSWORD,
        }
        payload.update(extra)
        return self.client.post(reverse("accounts:register"), payload)

    def test_a_student_must_supply_a_grade(self):
        response = self.register("gradeless", "student")

        self.assertEqual(response.status_code, 200)
        self.assertIn("grade", response.context["form"].errors)
        self.assertFalse(User.objects.filter(username="gradeless").exists())

    def test_a_primary_grade_is_stored_and_flags_the_band(self):
        self.register("primary-kid", "student", grade="4")

        student = User.objects.get(username="primary-kid")
        self.assertEqual(student.grade, 4)
        self.assertTrue(student.is_primary_student)
        self.assertFalse(student.is_secondary_student)

    def test_a_secondary_grade_is_stored_and_flags_the_band(self):
        self.register("secondary-kid", "student", grade="10")

        student = User.objects.get(username="secondary-kid")
        self.assertEqual(student.grade, 10)
        self.assertTrue(student.is_secondary_student)
        self.assertFalse(student.is_primary_student)

    def test_a_grade_outside_1_to_12_is_rejected(self):
        response = self.register("too-high", "student", grade="13")

        self.assertEqual(response.status_code, 200)
        self.assertIn("grade", response.context["form"].errors)
        self.assertFalse(User.objects.filter(username="too-high").exists())

    def test_grade_is_ignored_for_a_teacher(self):
        """A non-student who happens to send a grade is stored without one."""
        self.register("teacher-acct", "teacher", grade="8")

        teacher = User.objects.get(username="teacher-acct")
        self.assertIsNone(teacher.grade)

    def test_grade_is_ignored_for_a_parent(self):
        # A parent registration requires a phone number (Sprint 6); supply one
        # so the account is created and the grade-ignoring is what's exercised.
        self.register(
            "parent-acct", "parent", grade="8", phone_number="+27821234567"
        )

        self.assertIsNone(User.objects.get(username="parent-acct").grade)

    def test_the_registration_page_shows_a_grade_field(self):
        response = self.client.get(reverse("accounts:register"))
        self.assertContains(response, "Grade")
