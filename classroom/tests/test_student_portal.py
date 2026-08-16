"""Tests for the student portal: study materials, assignments, and submitting
homework.

The load-bearing test in this file is the spoofing one: a student must never be
able to submit work attributed to another student. It posts a tampered request
and confirms the server ignores it. Everything AI-facing is mocked, so no quota
is spent.
"""

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from classroom.models import Assignment, StudyMaterial
from marking.models import Paper
from marking.tests.support import (
    completion_response,
    error_response,
    make_memorandum,
    make_upload,
    make_user,
    valid_marking_json,
)


def make_assignment(teacher, title="Multiplication practice", **extra):
    return Assignment.objects.create(
        title=title,
        instructions="Complete questions 1 to 10 and show your working.",
        memorandum=extra.pop("memorandum", None) or make_memorandum(created_by=teacher),
        created_by=teacher,
        **extra,
    )


def make_material(teacher, title="How to add fractions"):
    return StudyMaterial.objects.create(
        title=title,
        content="First make the denominators the same, then add the numerators.",
        created_by=teacher,
    )


STUDENT_URLS = [
    ("classroom:student_material_list", {}),
    ("classroom:student_assignment_list", {}),
]


class StudentPortalAccessTests(TestCase):
    """Only students reach the student pages."""

    @classmethod
    def setUpTestData(cls):
        cls.teacher = make_user("sp-teacher", Role.TEACHER)
        cls.student = make_user("sp-student", Role.STUDENT)
        cls.parent = make_user("sp-parent", Role.PARENT)
        cls.material = make_material(cls.teacher)
        cls.assignment = make_assignment(cls.teacher)

    def all_urls(self):
        return STUDENT_URLS + [
            ("classroom:student_material_detail", {"pk": self.material.pk}),
            ("classroom:submit_homework", {"pk": self.assignment.pk}),
        ]

    def test_a_student_can_reach_every_page(self):
        self.client.force_login(self.student)
        for name, kwargs in self.all_urls():
            with self.subTest(url=name):
                self.assertEqual(
                    self.client.get(reverse(name, kwargs=kwargs)).status_code, 200
                )

    def test_a_teacher_is_refused_every_page(self):
        self.client.force_login(self.teacher)
        for name, kwargs in self.all_urls():
            with self.subTest(url=name):
                self.assertEqual(
                    self.client.get(reverse(name, kwargs=kwargs)).status_code, 403
                )

    def test_a_parent_is_refused_every_page(self):
        self.client.force_login(self.parent)
        for name, kwargs in self.all_urls():
            with self.subTest(url=name):
                self.assertEqual(
                    self.client.get(reverse(name, kwargs=kwargs)).status_code, 403
                )

    def test_an_anonymous_visitor_is_sent_to_login(self):
        for name, kwargs in self.all_urls():
            with self.subTest(url=name):
                response = self.client.get(reverse(name, kwargs=kwargs))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("accounts:login"), response.headers["Location"])


class StudyMaterialViewTests(TestCase):
    def setUp(self):
        self.teacher = make_user("mat-teacher", Role.TEACHER)
        self.student = make_user("mat-student", Role.STUDENT)
        self.client.force_login(self.student)

    def test_the_list_shows_every_material(self):
        make_material(self.teacher, title="Fractions")
        make_material(make_user("mat-teacher-2", Role.TEACHER), title="Long division")

        response = self.client.get(reverse("classroom:student_material_list"))

        # No class scoping yet: a student sees all posted material.
        self.assertContains(response, "Fractions")
        self.assertContains(response, "Long division")

    def test_the_detail_shows_the_content(self):
        material = make_material(self.teacher)
        response = self.client.get(
            reverse("classroom:student_material_detail", kwargs={"pk": material.pk})
        )
        self.assertContains(response, "denominators")

    def test_the_empty_state_is_friendly(self):
        response = self.client.get(reverse("classroom:student_material_list"))
        self.assertContains(response, "Nothing to read yet")


class AssignmentListStatusTests(TestCase):
    """The list must show, per assignment, whether this student has submitted."""

    def setUp(self):
        self.teacher = make_user("al-teacher", Role.TEACHER)
        self.student = make_user("al-student", Role.STUDENT)
        self.other = make_user("al-other", Role.STUDENT)
        self.client.force_login(self.student)

    def submitted_paper(self, assignment, student, status=Paper.Status.MARKED):
        return Paper.objects.create(
            memorandum=assignment.memorandum,
            submitted_by=student,
            student=student,
            assignment=assignment,
            image=make_upload(),
            status=status,
        )

    def test_an_unsubmitted_assignment_shows_not_yet_submitted(self):
        make_assignment(self.teacher, title="Homework A")

        response = self.client.get(reverse("classroom:student_assignment_list"))

        self.assertContains(response, "Homework A")
        self.assertContains(response, "Not yet submitted")

    def test_a_submitted_assignment_shows_submitted(self):
        assignment = make_assignment(self.teacher, title="Homework B")
        self.submitted_paper(assignment, self.student)

        response = self.client.get(reverse("classroom:student_assignment_list"))

        self.assertContains(response, "Submitted")
        self.assertNotContains(response, "Not yet submitted")

    def test_another_students_submission_does_not_count_as_mine(self):
        assignment = make_assignment(self.teacher, title="Homework C")
        # The *other* student submitted, not me.
        self.submitted_paper(assignment, self.other)

        response = self.client.get(reverse("classroom:student_assignment_list"))

        self.assertContains(response, "Not yet submitted")

    def test_the_latest_submission_status_is_the_one_shown(self):
        assignment = make_assignment(self.teacher, title="Homework D")
        self.submitted_paper(assignment, self.student, status=Paper.Status.FAILED)
        self.submitted_paper(assignment, self.student, status=Paper.Status.MARKED)

        response = self.client.get(reverse("classroom:student_assignment_list"))

        # Most recent is marked, so the marked/submitted state should show.
        self.assertContains(response, "Submitted")


class SubmitHomeworkTests(TestCase):
    def setUp(self):
        self.teacher = make_user("sh-teacher", Role.TEACHER)
        self.student = make_user("sh-student", Role.STUDENT)
        self.assignment = make_assignment(self.teacher)
        self.client.force_login(self.student)
        self.url = reverse(
            "classroom:submit_homework", kwargs={"pk": self.assignment.pk}
        )

    def submit(self, response=None, extra=None):
        data = {"image": make_upload()}
        if extra:
            data.update(extra)
        with patch("marking.openrouter.requests.post") as post:
            post.return_value = response or completion_response(valid_marking_json())
            self.post_mock = post
            return self.client.post(self.url, data)

    def test_a_student_can_submit_and_is_taken_to_the_result(self):
        response = self.submit()

        paper = Paper.objects.get()
        self.assertRedirects(
            response, reverse("marking:my_result_detail", kwargs={"pk": paper.pk})
        )
        self.assertEqual(paper.status, Paper.Status.MARKED)

    def test_the_submission_is_tied_to_the_assignment_and_its_memorandum(self):
        self.submit()

        paper = Paper.objects.get()
        self.assertEqual(paper.assignment, self.assignment)
        self.assertEqual(paper.memorandum, self.assignment.memorandum)

    def test_student_is_set_from_the_session_not_the_form(self):
        self.submit()

        paper = Paper.objects.get()
        self.assertEqual(paper.student, self.student)
        self.assertEqual(paper.submitted_by, self.student)

    def test_a_spoofed_student_id_is_ignored(self):
        """The security requirement: a student cannot submit as someone else."""
        victim = make_user("sh-victim", Role.STUDENT)
        other_assignment = make_assignment(self.teacher, title="Someone else's")

        # A tampered POST carrying fields the form does not define.
        self.submit(
            extra={
                "student": victim.pk,
                "student_id": victim.pk,
                "submitted_by": victim.pk,
                "assignment": other_assignment.pk,
            }
        )

        paper = Paper.objects.get()
        # The logged-in student owns it; the spoof was ignored end to end.
        self.assertEqual(paper.student, self.student)
        self.assertEqual(paper.submitted_by, self.student)
        self.assertNotEqual(paper.student, victim)
        # Assignment came from the URL, not the tampered body.
        self.assertEqual(paper.assignment, self.assignment)

    def test_a_non_image_is_reported_and_nothing_is_saved(self):
        response = self.submit(extra={"image": make_upload("hw.jpg", b"not an image")})

        self.assertEqual(response.status_code, 200)
        self.assertIn("image", response.context["form"].errors)
        self.assertFalse(Paper.objects.exists())

    def test_a_failed_marking_keeps_the_paper_and_shows_the_retry_state(self):
        response = self.submit(response=error_response(429, "busy"))

        paper = Paper.objects.get()
        self.assertEqual(paper.status, Paper.Status.FAILED)
        self.assertEqual(paper.failure_kind, Paper.FailureKind.RATE_LIMITED)

        # Following the redirect lands on the result page with the retry state.
        detail = self.client.get(
            reverse("marking:my_result_detail", kwargs={"pk": paper.pk})
        )
        self.assertContains(detail, "Marking failed")
        self.assertContains(detail, "Try marking again")
        self.assertNotContains(detail, "Traceback")
        self.assertNotContains(detail, "OpenRouter")

    def test_the_uploaded_image_is_compressed_before_marking(self):
        from marking.tests.support import make_image_bytes

        self.submit(extra={"image": make_upload(content=make_image_bytes(size=(3000, 2200)))})

        content = self.post_mock.call_args.kwargs["json"]["messages"][1]["content"]
        data_uri = next(p for p in content if p["type"] == "image_url")["image_url"]["url"]
        self.assertTrue(data_uri.startswith("data:image/jpeg;base64,"))
        self.assertTrue(Paper.objects.get().image.name.startswith("papers/"))

    def test_a_parent_cannot_submit_homework(self):
        self.client.force_login(make_user("sh-parent", Role.PARENT))
        with patch("marking.openrouter.requests.post") as post:
            post.return_value = completion_response(valid_marking_json())
            response = self.client.post(self.url, {"image": make_upload()})

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Paper.objects.exists())
        post.assert_not_called()
