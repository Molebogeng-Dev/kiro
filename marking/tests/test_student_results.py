"""Tests for a student viewing their own results.

The ownership boundary is the point: a student sees only papers where they are
the subject, and guessing another student's paper id returns a 404, not a
forbidden page that confirms it exists. Every AI call is mocked.
"""

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from marking.models import MarkingResult, Paper, QuestionResult

from .support import (
    completion_response,
    error_response,
    make_memorandum,
    make_upload,
    make_user,
    valid_marking_json,
)


def make_student_paper(student, teacher, status=Paper.Status.PENDING, **extra):
    """A paper belonging to ``student``.

    When ``status`` is MARKED a result is attached, because a marked paper always
    has one in reality — the engine writes both in a single transaction. Faking a
    marked paper without a result would test a state that cannot occur.
    """
    paper = Paper.objects.create(
        memorandum=make_memorandum(created_by=teacher),
        submitted_by=extra.pop("submitted_by", student),
        student=student,
        image=make_upload(),
        status=status,
        **extra,
    )
    if status == Paper.Status.MARKED:
        result = MarkingResult.objects.create(
            paper=paper,
            marks_awarded=4,
            marks_available=6,
            summary="A solid effort.",
            model_used="test/model",
        )
        QuestionResult.objects.create(
            result=result,
            number="1",
            marks_awarded=4,
            marks_available=6,
            feedback="Well reasoned.",
            position=1,
        )
    return paper


class StudentResultsAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = make_user("sr-teacher", Role.TEACHER)
        cls.student = make_user("sr-student", Role.STUDENT)
        cls.parent = make_user("sr-parent", Role.PARENT)
        cls.paper = make_student_paper(cls.student, cls.teacher)

    def urls(self):
        return [
            reverse("marking:my_results"),
            reverse("marking:my_result_detail", kwargs={"pk": self.paper.pk}),
        ]

    def test_a_student_can_reach_their_results(self):
        self.client.force_login(self.student)
        for url in self.urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_a_teacher_is_refused(self):
        self.client.force_login(self.teacher)
        for url in self.urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_a_parent_is_refused(self):
        self.client.force_login(self.parent)
        for url in self.urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_an_anonymous_visitor_is_sent_to_login(self):
        for url in self.urls():
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("accounts:login"), response.headers["Location"])


class StudentResultsOwnershipTests(TestCase):
    """A student sees their own work and nobody else's."""

    def setUp(self):
        self.teacher = make_user("own-teacher", Role.TEACHER)
        self.student = make_user("own-student", Role.STUDENT)
        self.other = make_user("own-other", Role.STUDENT)
        self.client.force_login(self.student)

    def test_the_list_shows_only_my_papers(self):
        mine = make_student_paper(self.student, self.teacher)
        theirs = make_student_paper(self.other, self.teacher)

        response = self.client.get(reverse("marking:my_results"))

        papers = list(response.context["papers"])
        self.assertIn(mine, papers)
        self.assertNotIn(theirs, papers)

    def test_i_cannot_open_another_students_paper_even_by_guessing(self):
        theirs = make_student_paper(self.other, self.teacher)

        response = self.client.get(
            reverse("marking:my_result_detail", kwargs={"pk": theirs.pk})
        )
        # 404, not 403: the filter excludes it before the lookup, so we never
        # confirm the paper exists.
        self.assertEqual(response.status_code, 404)

    def test_i_cannot_retry_another_students_paper(self):
        theirs = make_student_paper(
            self.other, self.teacher, status=Paper.Status.FAILED
        )
        response = self.client.post(
            reverse("marking:my_result_retry", kwargs={"pk": theirs.pk})
        )
        self.assertEqual(response.status_code, 404)

    def test_newest_paper_comes_first(self):
        older = make_student_paper(self.student, self.teacher)
        newer = make_student_paper(self.student, self.teacher)

        papers = list(self.client.get(reverse("marking:my_results")).context["papers"])

        self.assertEqual(papers[0].pk, newer.pk)
        self.assertEqual(papers[-1].pk, older.pk)

    def test_a_paper_a_teacher_uploaded_for_me_is_mine_to_see(self):
        """Ownership is about being the subject, not the uploader."""
        paper = make_student_paper(
            self.student, self.teacher, submitted_by=self.teacher
        )

        response = self.client.get(
            reverse("marking:my_result_detail", kwargs={"pk": paper.pk})
        )
        self.assertEqual(response.status_code, 200)


class StudentResultDetailTests(TestCase):
    def setUp(self):
        self.teacher = make_user("srd-teacher", Role.TEACHER)
        self.student = make_user("srd-student", Role.STUDENT)
        self.client.force_login(self.student)

    def test_a_marked_paper_shows_score_and_per_question_feedback(self):
        from marking.engine import mark_paper

        paper = make_student_paper(
            self.student, self.teacher, status=Paper.Status.PENDING
        )
        with patch("marking.openrouter.requests.post") as post:
            post.return_value = completion_response(valid_marking_json())
            mark_paper(paper, image_bytes=b"jpeg-bytes")

        response = self.client.get(
            reverse("marking:my_result_detail", kwargs={"pk": paper.pk})
        )
        self.assertContains(response, "66.7")
        self.assertContains(response, "Question 1.1")
        self.assertContains(response, "six times table")

    def test_a_failed_paper_shows_the_retry_state(self):
        paper = make_student_paper(
            self.student, self.teacher, status=Paper.Status.FAILED
        )
        paper.failure_kind = Paper.FailureKind.RATE_LIMITED
        paper.save(update_fields=["failure_kind"])

        response = self.client.get(
            reverse("marking:my_result_detail", kwargs={"pk": paper.pk})
        )
        self.assertContains(response, "Marking failed")
        self.assertContains(response, "Try marking again")

    def test_retrying_my_failed_paper_can_succeed(self):
        paper = make_student_paper(
            self.student, self.teacher, status=Paper.Status.FAILED
        )
        paper.failure_kind = Paper.FailureKind.RATE_LIMITED
        paper.save(update_fields=["failure_kind"])

        with patch("marking.openrouter.requests.post") as post:
            post.return_value = completion_response(valid_marking_json())
            response = self.client.post(
                reverse("marking:my_result_retry", kwargs={"pk": paper.pk}),
                follow=True,
            )

        paper.refresh_from_db()
        self.assertEqual(paper.status, Paper.Status.MARKED)
        self.assertContains(response, "66.7")

    def test_retry_must_be_a_post(self):
        paper = make_student_paper(
            self.student, self.teacher, status=Paper.Status.FAILED
        )
        response = self.client.get(
            reverse("marking:my_result_retry", kwargs={"pk": paper.pk})
        )
        self.assertEqual(response.status_code, 405)
