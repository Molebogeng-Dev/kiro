"""Tests for the teacher-facing marking portal.

Three things these guard. First, that only a teacher can reach any of it, because
these pages read learners' marked work and spend money on an AI provider. Second,
that a marked paper is attached to the learner it belongs to, which is what makes
the result reach them and their parent later. Third, that a failure is a page a
teacher can act on rather than a dead end.

Every OpenRouter call is mocked. The suite spends no quota.
"""

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from marking.models import MarkingResult, Memorandum, Paper

from .support import (
    completion_response,
    error_response,
    make_image_bytes,
    make_memorandum,
    make_upload,
    make_user,
    valid_marking_json,
)

# Every page added this sprint, so a new one cannot quietly skip the role check.
TEACHER_ONLY_URLS = [
    ("marking:memorandum_list", {}),
    ("marking:memorandum_create", {}),
    ("marking:mark_paper", {}),
    ("marking:marking_history", {}),
]


class TeacherPortalAccessTests(TestCase):
    """Non-teachers never reach these pages, by URL or otherwise."""

    @classmethod
    def setUpTestData(cls):
        cls.teacher = make_user("portal-teacher", Role.TEACHER)
        cls.student = make_user("portal-student", Role.STUDENT)
        cls.parent = make_user("portal-parent", Role.PARENT)

    def test_a_teacher_can_reach_every_page(self):
        self.client.force_login(self.teacher)
        for name, kwargs in TEACHER_ONLY_URLS:
            with self.subTest(url=name):
                response = self.client.get(reverse(name, kwargs=kwargs))
                self.assertEqual(response.status_code, 200)

    def test_a_student_is_refused_every_page(self):
        self.client.force_login(self.student)
        for name, kwargs in TEACHER_ONLY_URLS:
            with self.subTest(url=name):
                self.assertEqual(
                    self.client.get(reverse(name, kwargs=kwargs)).status_code, 403
                )

    def test_a_parent_is_refused_every_page(self):
        self.client.force_login(self.parent)
        for name, kwargs in TEACHER_ONLY_URLS:
            with self.subTest(url=name):
                self.assertEqual(
                    self.client.get(reverse(name, kwargs=kwargs)).status_code, 403
                )

    def test_an_anonymous_visitor_is_sent_to_login(self):
        for name, kwargs in TEACHER_ONLY_URLS:
            with self.subTest(url=name):
                response = self.client.get(reverse(name, kwargs=kwargs))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("accounts:login"), response.headers["Location"])

    def test_a_student_cannot_post_a_memorandum(self):
        """The role check covers POST, not only the page they can see."""
        self.client.force_login(self.student)
        response = self.client.post(
            reverse("marking:memorandum_create"),
            {"title": "Sneaky", "content": "x" * 40, "subject": "", "total_marks": ""},
        )

        self.assertEqual(response.status_code, 403)
        # Scoped rather than a global .exists(), so a shared or reused test
        # database (a --keepdb Postgres run) cannot make this pass or fail by
        # accident.
        self.assertFalse(Memorandum.objects.filter(title="Sneaky").exists())


class MemorandumAuthoringTests(TestCase):
    """A teacher can write a marking guide without touching the admin."""

    def setUp(self):
        self.teacher = make_user("memo-teacher", Role.TEACHER)
        self.client.force_login(self.teacher)

    def valid_payload(self, **overrides):
        payload = {
            "title": "Grade 5 Mathematics Test 2",
            "subject": "Mathematics",
            "total_marks": 10,
            "content": (
                "Question 1.1 (2 marks)\nWhat is 9 x 4?\nExpected answer: 36\n\n"
                "Question 1.2 (3 marks)\nWhat is 120 / 4?\nExpected answer: 30"
            ),
        }
        payload.update(overrides)
        return payload

    def test_a_memorandum_is_saved_against_the_teacher_who_wrote_it(self):
        response = self.client.post(
            reverse("marking:memorandum_create"), self.valid_payload()
        )

        self.assertRedirects(response, reverse("marking:memorandum_list"))
        memorandum = Memorandum.objects.get(created_by=self.teacher)
        self.assertEqual(memorandum.title, "Grade 5 Mathematics Test 2")
        self.assertEqual(memorandum.created_by, self.teacher)
        self.assertEqual(memorandum.total_marks, 10)
        self.assertIn("9 x 4", memorandum.content)

    def test_the_teacher_is_told_it_saved(self):
        response = self.client.post(
            reverse("marking:memorandum_create"), self.valid_payload(), follow=True
        )
        self.assertContains(response, "Grade 5 Mathematics Test 2")

    def test_optional_fields_can_be_left_blank(self):
        self.client.post(
            reverse("marking:memorandum_create"),
            self.valid_payload(subject="", total_marks=""),
        )

        memorandum = Memorandum.objects.get(created_by=self.teacher)
        # Subject is still optional on the form, but a blank one now files under
        # the default rather than an empty string, so the progress dashboard has
        # a subject to group by (Sprint 7).
        self.assertEqual(memorandum.subject, "General")
        self.assertIsNone(memorandum.total_marks)

    def test_a_guide_too_short_to_mark_against_is_rejected(self):
        response = self.client.post(
            reverse("marking:memorandum_create"), self.valid_payload(content="42")
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("content", response.context["form"].errors)
        self.assertFalse(Memorandum.objects.filter(created_by=self.teacher).exists())

    def test_a_missing_title_is_rejected(self):
        response = self.client.post(
            reverse("marking:memorandum_create"), self.valid_payload(title="")
        )

        self.assertIn("title", response.context["form"].errors)
        self.assertFalse(Memorandum.objects.filter(created_by=self.teacher).exists())

    def test_the_list_shows_only_this_teachers_memorandums(self):
        mine = make_memorandum(title="Mine", created_by=self.teacher)
        theirs = make_memorandum(
            title="Someone else's", created_by=make_user("other-teacher", Role.TEACHER)
        )

        response = self.client.get(reverse("marking:memorandum_list"))

        self.assertContains(response, mine.title)
        self.assertNotContains(response, theirs.title)

    def test_the_empty_list_explains_what_a_memorandum_is_for(self):
        response = self.client.get(reverse("marking:memorandum_list"))
        self.assertContains(response, "No memorandums yet")


class MarkPaperFlowTests(TestCase):
    """The main event: photograph a learner's paper and get marks back."""

    def setUp(self):
        self.teacher = make_user("marking-teacher", Role.TEACHER)
        self.learner = make_user("thabo", Role.STUDENT, first_name="Thabo", last_name="M")
        self.memorandum = make_memorandum(created_by=self.teacher)
        self.client.force_login(self.teacher)
        self.url = reverse("marking:mark_paper")

    def submit(self, response=None, **overrides):
        payload = {
            "student": self.learner.pk,
            "memorandum": self.memorandum.pk,
            "image": make_upload(),
        }
        payload.update(overrides)

        with patch("marking.openrouter.requests.post") as post:
            post.return_value = response or completion_response(valid_marking_json())
            self.post_mock = post
            return self.client.post(self.url, payload)

    def test_the_form_offers_learners_to_choose_from(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Thabo M")
        self.assertContains(response, "Whose paper is this?")

    def test_a_marked_paper_is_attached_to_the_learner_not_the_teacher(self):
        """Without this the mark cannot reach the learner or their parent."""
        self.submit()

        paper = Paper.objects.get()
        self.assertEqual(paper.student, self.learner)
        self.assertEqual(paper.submitted_by, self.teacher)
        self.assertEqual(paper.status, Paper.Status.MARKED)

    def test_marking_redirects_to_the_result_page(self):
        response = self.submit()
        paper = Paper.objects.get()

        self.assertRedirects(
            response, reverse("marking:paper_detail", kwargs={"pk": paper.pk})
        )

    def test_the_result_page_shows_the_score_and_every_question(self):
        self.submit()
        paper = Paper.objects.get()

        response = self.client.get(
            reverse("marking:paper_detail", kwargs={"pk": paper.pk})
        )

        self.assertContains(response, "Thabo M")
        self.assertContains(response, "66.7")
        self.assertContains(response, "Question 1.1")
        self.assertContains(response, "Question 1.2")
        # The feedback, not just the number, is the point of the app.
        self.assertContains(response, "six times table")

    def test_a_learner_is_required(self):
        response = self.submit(student="")

        self.assertEqual(response.status_code, 200)
        self.assertIn("student", response.context["form"].errors)
        self.assertFalse(Paper.objects.exists())

    def test_a_memorandum_is_required(self):
        response = self.submit(memorandum="")

        self.assertEqual(response.status_code, 200)
        self.assertIn("memorandum", response.context["form"].errors)
        self.assertFalse(Paper.objects.exists())

    def test_a_file_that_is_not_an_image_is_reported_on_the_field(self):
        response = self.submit(image=make_upload("paper.jpg", b"not an image"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("image", response.context["form"].errors)
        self.assertFalse(Paper.objects.exists())

    def test_only_learners_can_be_chosen(self):
        """A teacher or parent id in the dropdown must not be accepted."""
        another_teacher = make_user("not-a-learner", Role.TEACHER)
        response = self.submit(student=another_teacher.pk)

        self.assertEqual(response.status_code, 200)
        self.assertIn("student", response.context["form"].errors)
        self.assertFalse(Paper.objects.exists())

    def test_the_image_is_compressed_before_it_leaves_the_server(self):
        self.submit(image=make_upload(content=make_image_bytes(size=(3000, 2200))))

        content = self.post_mock.call_args.kwargs["json"]["messages"][1]["content"]
        data_uri = next(p for p in content if p["type"] == "image_url")["image_url"]["url"]
        self.assertTrue(data_uri.startswith("data:image/jpeg;base64,"))

        paper = Paper.objects.get()
        self.assertTrue(paper.image.name.startswith("papers/"))


class MarkingFailureStateTests(TestCase):
    """A failure has to be a page with a way forward, not a stack trace."""

    def setUp(self):
        self.teacher = make_user("failing-teacher", Role.TEACHER)
        self.learner = make_user("learner-b", Role.STUDENT, first_name="Naledi")
        self.memorandum = make_memorandum(created_by=self.teacher)
        self.client.force_login(self.teacher)

    def submit_failing(self, response=None):
        with patch("marking.openrouter.requests.post") as post:
            post.return_value = response or error_response(429, "rate limited")
            return self.client.post(
                reverse("marking:mark_paper"),
                {
                    "student": self.learner.pk,
                    "memorandum": self.memorandum.pk,
                    "image": make_upload(),
                },
                follow=True,
            )

    def test_a_failed_marking_still_keeps_the_paper(self):
        self.submit_failing()

        paper = Paper.objects.get()
        self.assertEqual(paper.status, Paper.Status.FAILED)
        self.assertEqual(paper.failure_kind, Paper.FailureKind.RATE_LIMITED)
        self.assertTrue(paper.image.name)

    def test_the_teacher_sees_a_plain_explanation_and_no_stack_trace(self):
        response = self.submit_failing()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Marking failed")
        self.assertContains(response, "busy at the moment")
        self.assertContains(response, "Try marking again")
        # No provider names, status codes, or Python in front of a teacher.
        self.assertNotContains(response, "Traceback")
        self.assertNotContains(response, "OpenRouter")
        self.assertNotContains(response, "429")

    def test_the_photo_does_not_have_to_be_taken_again(self):
        response = self.submit_failing()
        self.assertContains(response, "The paper is safe")

    def test_retrying_can_succeed_and_shows_the_result(self):
        self.submit_failing()
        paper = Paper.objects.get()

        with patch("marking.openrouter.requests.post") as post:
            post.return_value = completion_response(valid_marking_json())
            response = self.client.post(
                reverse("marking:paper_retry", kwargs={"pk": paper.pk}), follow=True
            )

        paper.refresh_from_db()
        self.assertEqual(paper.status, Paper.Status.MARKED)
        self.assertEqual(paper.failure_kind, "")
        self.assertContains(response, "66.7")
        self.assertEqual(MarkingResult.objects.count(), 1)

    def test_a_retry_that_fails_again_says_so_without_losing_the_paper(self):
        self.submit_failing()
        paper = Paper.objects.get()

        with patch("marking.openrouter.requests.post") as post:
            post.return_value = error_response(429)
            response = self.client.post(
                reverse("marking:paper_retry", kwargs={"pk": paper.pk}), follow=True
            )

        paper.refresh_from_db()
        self.assertEqual(paper.status, Paper.Status.FAILED)
        self.assertContains(response, "Marking failed")
        self.assertTrue(Paper.objects.filter(pk=paper.pk).exists())

    def test_a_failure_that_retrying_cannot_fix_does_not_offer_the_button(self):
        """An empty account fails identically until somebody tops it up."""
        self.submit_failing(response=error_response(402, "no credit"))
        response = self.client.get(
            reverse(
                "marking:paper_detail", kwargs={"pk": Paper.objects.get().pk}
            )
        )

        self.assertContains(response, "run out of credit")
        self.assertNotContains(response, "Try marking again")

    def test_retry_must_be_a_post(self):
        self.submit_failing()
        paper = Paper.objects.get()

        response = self.client.get(
            reverse("marking:paper_retry", kwargs={"pk": paper.pk})
        )
        self.assertEqual(response.status_code, 405)


class MarkingHistoryTests(TestCase):
    def setUp(self):
        self.teacher = make_user("history-teacher", Role.TEACHER)
        self.other_teacher = make_user("history-other", Role.TEACHER)
        self.learner = make_user("history-learner", Role.STUDENT, first_name="Sipho")
        self.memorandum = make_memorandum(created_by=self.teacher)
        self.client.force_login(self.teacher)

    def make_paper(self, submitted_by=None, status=Paper.Status.MARKED):
        return Paper.objects.create(
            memorandum=self.memorandum,
            submitted_by=submitted_by or self.teacher,
            student=self.learner,
            image=make_upload(),
            status=status,
        )

    def test_history_lists_this_teachers_papers(self):
        self.make_paper()
        response = self.client.get(reverse("marking:marking_history"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sipho")

    def test_history_hides_another_teachers_papers(self):
        self.make_paper(submitted_by=self.other_teacher)

        response = self.client.get(reverse("marking:marking_history"))

        self.assertEqual(len(response.context["papers"]), 0)
        self.assertContains(response, "not marked anything yet")

    def test_the_newest_paper_comes_first(self):
        older = self.make_paper()
        newer = self.make_paper()

        papers = list(self.client.get(reverse("marking:marking_history")).context["papers"])

        self.assertEqual(papers[0].pk, newer.pk)
        self.assertEqual(papers[-1].pk, older.pk)

    def test_a_failed_paper_is_shown_as_needing_another_try(self):
        self.make_paper(status=Paper.Status.FAILED)

        response = self.client.get(reverse("marking:marking_history"))

        self.assertContains(response, "Marking failed")
        self.assertEqual(response.context["failed_count"], 1)

    def test_another_teacher_cannot_open_your_paper(self):
        paper = self.make_paper()
        self.client.force_login(self.other_teacher)

        response = self.client.get(
            reverse("marking:paper_detail", kwargs={"pk": paper.pk})
        )
        self.assertEqual(response.status_code, 404)

    def test_a_student_cannot_open_a_paper_page(self):
        paper = self.make_paper()
        self.client.force_login(make_user("nosy-learner", Role.STUDENT))

        response = self.client.get(
            reverse("marking:paper_detail", kwargs={"pk": paper.pk})
        )
        self.assertEqual(response.status_code, 403)


class TeacherDashboardTests(TestCase):
    """The hub should tell a teacher what to do next, whatever state they are in."""

    def setUp(self):
        self.teacher = make_user("dash-teacher", Role.TEACHER)
        self.client.force_login(self.teacher)

    def test_a_new_teacher_is_pointed_at_writing_a_memorandum(self):
        response = self.client.get(reverse("core:teacher_dashboard"))

        self.assertContains(response, "Welcome, Teacher")
        self.assertContains(response, "Write your first memorandum")

    def test_a_teacher_with_a_memorandum_is_pointed_at_marking(self):
        make_memorandum(created_by=self.teacher)

        response = self.client.get(reverse("core:teacher_dashboard"))

        self.assertContains(response, "Mark your first paper")

    def test_recently_marked_papers_appear_on_the_dashboard(self):
        learner = make_user("dash-learner", Role.STUDENT, first_name="Ayanda")
        Paper.objects.create(
            memorandum=make_memorandum(created_by=self.teacher),
            submitted_by=self.teacher,
            student=learner,
            image=make_upload(),
            status=Paper.Status.MARKED,
        )

        response = self.client.get(reverse("core:teacher_dashboard"))

        self.assertContains(response, "Ayanda")
        self.assertEqual(len(response.context["recent_papers"]), 1)

    def test_the_dashboard_links_to_every_teacher_task(self):
        response = self.client.get(reverse("core:teacher_dashboard"))

        for url_name in (
            "marking:mark_paper",
            "marking:memorandum_create",
            "classroom:assignment_create",
            "classroom:material_create",
        ):
            with self.subTest(url=url_name):
                self.assertContains(response, reverse(url_name))
