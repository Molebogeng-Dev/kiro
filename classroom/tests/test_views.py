"""Tests for posting assignments and study material.

The persistence assertions matter more than they look. A teacher who posts work
and cannot tell whether it saved will post it again, so these check both that the
record exists with the right owner and that the teacher is shown it afterwards.
"""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from classroom.models import Assignment, StudyMaterial
from marking.tests.support import make_memorandum, make_user

TEACHER_ONLY_URLS = [
    "classroom:assignment_list",
    "classroom:assignment_create",
    "classroom:material_list",
    "classroom:material_create",
]


class ClassroomAccessTests(TestCase):
    """Posting to learners is a teacher's job, enforced at the view."""

    @classmethod
    def setUpTestData(cls):
        cls.teacher = make_user("classroom-teacher", Role.TEACHER)
        cls.student = make_user("classroom-student", Role.STUDENT)
        cls.parent = make_user("classroom-parent", Role.PARENT)
        cls.memorandum = make_memorandum(created_by=cls.teacher)

    def test_a_teacher_can_reach_every_page(self):
        self.client.force_login(self.teacher)
        for name in TEACHER_ONLY_URLS:
            with self.subTest(url=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_a_student_is_refused_every_page(self):
        self.client.force_login(self.student)
        for name in TEACHER_ONLY_URLS:
            with self.subTest(url=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_a_parent_is_refused_every_page(self):
        self.client.force_login(self.parent)
        for name in TEACHER_ONLY_URLS:
            with self.subTest(url=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_an_anonymous_visitor_is_sent_to_login(self):
        for name in TEACHER_ONLY_URLS:
            with self.subTest(url=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("accounts:login"), response.headers["Location"])

    def test_a_student_cannot_post_an_assignment(self):
        self.client.force_login(self.student)
        response = self.client.post(
            reverse("classroom:assignment_create"),
            {
                "title": "Sneaky homework",
                "memorandum": self.memorandum.pk,
                "instructions": "Do the thing.",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Assignment.objects.exists())

    def test_a_student_cannot_post_study_material(self):
        self.client.force_login(self.student)
        response = self.client.post(
            reverse("classroom:material_create"),
            {"title": "Sneaky notes", "content": "x" * 40},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(StudyMaterial.objects.exists())


class AssignmentTests(TestCase):
    def setUp(self):
        self.teacher = make_user("assign-teacher", Role.TEACHER)
        self.memorandum = make_memorandum(created_by=self.teacher)
        self.client.force_login(self.teacher)

    def payload(self, **overrides):
        data = {
            "title": "Multiplication practice, questions 1 to 10",
            "memorandum": self.memorandum.pk,
            "instructions": "Complete questions 1 to 10 and show your working.",
            "due_date": (timezone.localdate() + timedelta(days=7)).isoformat(),
        }
        data.update(overrides)
        return data

    def test_an_assignment_persists_with_its_memorandum_and_owner(self):
        response = self.client.post(
            reverse("classroom:assignment_create"), self.payload()
        )

        self.assertRedirects(response, reverse("classroom:assignment_list"))
        assignment = Assignment.objects.get()
        self.assertEqual(assignment.title, "Multiplication practice, questions 1 to 10")
        self.assertEqual(assignment.memorandum, self.memorandum)
        self.assertEqual(assignment.created_by, self.teacher)
        self.assertEqual(assignment.due_date, timezone.localdate() + timedelta(days=7))

    def test_the_due_date_is_optional(self):
        self.client.post(reverse("classroom:assignment_create"), self.payload(due_date=""))

        assignment = Assignment.objects.get()
        self.assertIsNone(assignment.due_date)
        self.assertFalse(assignment.is_past_due)

    def test_a_memorandum_is_required(self):
        """An assignment with no marking guide could never be marked."""
        response = self.client.post(
            reverse("classroom:assignment_create"), self.payload(memorandum="")
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("memorandum", response.context["form"].errors)
        self.assertFalse(Assignment.objects.exists())

    def test_instructions_are_required(self):
        response = self.client.post(
            reverse("classroom:assignment_create"), self.payload(instructions="")
        )

        self.assertIn("instructions", response.context["form"].errors)
        self.assertFalse(Assignment.objects.exists())

    def test_a_due_date_in_the_past_is_caught_as_a_likely_typo(self):
        response = self.client.post(
            reverse("classroom:assignment_create"),
            self.payload(due_date=(timezone.localdate() - timedelta(days=3)).isoformat()),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("due_date", response.context["form"].errors)
        self.assertFalse(Assignment.objects.exists())

    def test_the_teacher_sees_the_assignment_afterwards(self):
        response = self.client.post(
            reverse("classroom:assignment_create"), self.payload(), follow=True
        )

        self.assertContains(response, "Multiplication practice, questions 1 to 10")
        self.assertContains(response, self.memorandum.title)

    def test_the_list_shows_only_this_teachers_assignments(self):
        Assignment.objects.create(
            title="Mine",
            instructions="Do it.",
            memorandum=self.memorandum,
            created_by=self.teacher,
        )
        Assignment.objects.create(
            title="Someone else's",
            instructions="Do it.",
            memorandum=self.memorandum,
            created_by=make_user("assign-other", Role.TEACHER),
        )

        response = self.client.get(reverse("classroom:assignment_list"))

        self.assertContains(response, "Mine")
        self.assertNotContains(response, "Someone else's")

    def test_the_newest_assignment_comes_first(self):
        older = Assignment.objects.create(
            title="Older", instructions="x", memorandum=self.memorandum, created_by=self.teacher
        )
        newer = Assignment.objects.create(
            title="Newer", instructions="x", memorandum=self.memorandum, created_by=self.teacher
        )

        assignments = list(
            self.client.get(reverse("classroom:assignment_list")).context["assignments"]
        )

        self.assertEqual(assignments[0].pk, newer.pk)
        self.assertEqual(assignments[-1].pk, older.pk)

    def test_an_overdue_assignment_reports_itself_as_past_due(self):
        assignment = Assignment.objects.create(
            title="Late",
            instructions="x",
            memorandum=self.memorandum,
            created_by=self.teacher,
            due_date=timezone.localdate() - timedelta(days=1),
        )

        self.assertTrue(assignment.is_past_due)
        self.assertEqual(assignment.days_until_due, -1)

    def test_the_form_warns_when_there_is_no_memorandum_to_choose(self):
        self.memorandum.delete()

        response = self.client.get(reverse("classroom:assignment_create"))

        self.assertFalse(response.context["has_memorandums"])
        self.assertContains(response, "needs a memorandum")


class StudyMaterialTests(TestCase):
    def setUp(self):
        self.teacher = make_user("material-teacher", Role.TEACHER)
        self.client.force_login(self.teacher)

    def payload(self, **overrides):
        data = {
            "title": "How to add fractions, step by step",
            "content": (
                "First make the denominators the same. Multiply the top and "
                "bottom of each fraction so both have the same bottom number."
            ),
        }
        data.update(overrides)
        return data

    def test_study_material_persists_against_the_teacher(self):
        response = self.client.post(
            reverse("classroom:material_create"), self.payload()
        )

        self.assertRedirects(response, reverse("classroom:material_list"))
        material = StudyMaterial.objects.get()
        self.assertEqual(material.title, "How to add fractions, step by step")
        self.assertEqual(material.created_by, self.teacher)
        self.assertIn("denominators", material.content)

    def test_a_title_is_required(self):
        response = self.client.post(
            reverse("classroom:material_create"), self.payload(title="")
        )

        self.assertIn("title", response.context["form"].errors)
        self.assertFalse(StudyMaterial.objects.exists())

    def test_content_too_short_to_help_anyone_is_rejected(self):
        response = self.client.post(
            reverse("classroom:material_create"), self.payload(content="Read chapter 4")
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("content", response.context["form"].errors)
        self.assertFalse(StudyMaterial.objects.exists())

    def test_the_teacher_sees_the_material_afterwards(self):
        response = self.client.post(
            reverse("classroom:material_create"), self.payload(), follow=True
        )
        self.assertContains(response, "How to add fractions, step by step")

    def test_the_list_shows_only_this_teachers_material(self):
        StudyMaterial.objects.create(
            title="Mine", content="x" * 40, created_by=self.teacher
        )
        StudyMaterial.objects.create(
            title="Someone else's",
            content="x" * 40,
            created_by=make_user("material-other", Role.TEACHER),
        )

        response = self.client.get(reverse("classroom:material_list"))

        self.assertContains(response, "Mine")
        self.assertNotContains(response, "Someone else's")

    def test_reading_time_is_estimated_for_learners(self):
        material = StudyMaterial.objects.create(
            title="Long read", content=" ".join(["word"] * 600), created_by=self.teacher
        )
        self.assertEqual(material.reading_time_minutes, 3)

    def test_short_material_still_reports_at_least_a_minute(self):
        material = StudyMaterial.objects.create(
            title="Short", content="a few words only here", created_by=self.teacher
        )
        self.assertEqual(material.reading_time_minutes, 1)


class TeacherOwnershipTests(TestCase):
    """Only a teacher account can own posted content, enforced in the model."""

    def test_a_student_cannot_be_recorded_as_the_author(self):
        from django.core.exceptions import ValidationError

        student = make_user("model-student", Role.STUDENT)
        material = StudyMaterial(title="x", content="y" * 40, created_by=student)

        with self.assertRaises(ValidationError):
            material.full_clean()

    def test_an_assignment_cannot_be_authored_by_a_parent(self):
        from django.core.exceptions import ValidationError

        parent = make_user("model-parent", Role.PARENT)
        teacher = make_user("model-teacher", Role.TEACHER)
        assignment = Assignment(
            title="x",
            instructions="y",
            memorandum=make_memorandum(created_by=teacher),
            created_by=parent,
        )

        with self.assertRaises(ValidationError):
            assignment.full_clean()
