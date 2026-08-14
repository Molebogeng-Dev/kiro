"""Tests for the marking endpoint.

Covers the whole path with only the HTTP call to OpenRouter mocked: upload,
Pillow processing, storage, the engine, parsing, and the JSON response. No API
calls and no network.
"""

import json
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from marking.models import Paper

from .support import (
    PASSWORD,
    completion_response,
    error_response,
    make_image_bytes,
    make_memorandum,
    make_upload,
    make_user,
    valid_marking_json,
)


class SubmitPaperTestCase(TestCase):
    def setUp(self):
        self.url = reverse("marking:submit_paper")
        self.memorandum = make_memorandum()
        self.teacher = make_user("view-teacher", Role.TEACHER)

    def submit(self, user=None, upload=None, response=None, memorandum=None):
        self.client.force_login(user or self.teacher)
        with patch("marking.openrouter.requests.post") as post:
            post.return_value = response or completion_response(valid_marking_json())
            http_response = self.client.post(
                self.url,
                {
                    "memorandum": (memorandum or self.memorandum).pk,
                    "image": upload or make_upload(),
                },
            )
        return http_response


class AccessControlTests(SubmitPaperTestCase):
    """The endpoint spends money and writes to storage, so it is not open."""

    def test_an_anonymous_visitor_is_sent_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.headers["Location"])

    def test_a_parent_cannot_submit_a_paper(self):
        self.client.force_login(make_user("view-parent", Role.PARENT))
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_a_teacher_can_reach_the_form(self):
        self.client.force_login(self.teacher)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mark a paper")

    def test_a_student_can_reach_the_form(self):
        self.client.force_login(make_user("view-student", Role.STUDENT))
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_an_unauthenticated_post_creates_nothing(self):
        self.client.post(self.url, {"memorandum": self.memorandum.pk, "image": make_upload()})
        self.assertEqual(Paper.objects.count(), 0)


class SuccessfulSubmissionTests(SubmitPaperTestCase):
    def test_a_submission_returns_the_marking_result(self):
        response = self.submit()
        body = response.json()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(body["result"]["marks_awarded"], 4.0)
        self.assertEqual(body["result"]["marks_available"], 6.0)
        self.assertEqual(body["result"]["percentage"], 66.7)
        self.assertEqual(len(body["result"]["questions"]), 2)

    def test_the_response_explains_why_marks_were_lost(self):
        body = self.submit().json()
        lost = [q for q in body["result"]["questions"] if q["marks_awarded"] < q["marks_available"]]

        self.assertTrue(lost)
        self.assertIn("six times table", lost[0]["feedback"])

    def test_the_paper_is_recorded_as_marked(self):
        body = self.submit().json()
        paper = Paper.objects.get(pk=body["paper"]["id"])

        self.assertEqual(paper.status, Paper.Status.MARKED)
        self.assertEqual(paper.submitted_by, self.teacher)
        self.assertEqual(paper.memorandum, self.memorandum)

    def test_the_response_reports_the_compression_that_happened(self):
        upload = make_upload(content=make_image_bytes(size=(3000, 2200)))
        body = self.submit(upload=upload).json()
        image = body["paper"]["image"]

        self.assertEqual(max(image["width"], image["height"]), 1600)
        self.assertLess(image["stored_bytes"], image["original_bytes"])
        self.assertGreater(image["reduction_percent"], 0)

    def test_the_stored_path_is_a_bucket_path_not_a_local_one(self):
        body = self.submit().json()

        self.assertTrue(body["paper"]["stored_path"].startswith("papers/"))
        self.assertNotIn("..", body["paper"]["stored_path"])

    def test_the_image_sent_for_marking_is_the_compressed_one(self):
        """The model must not receive an empty or full-resolution image."""
        self.client.force_login(self.teacher)
        with patch("marking.openrouter.requests.post") as post:
            post.return_value = completion_response(valid_marking_json())
            self.client.post(
                self.url,
                {
                    "memorandum": self.memorandum.pk,
                    "image": make_upload(content=make_image_bytes(size=(3000, 2200))),
                },
            )

        content = post.call_args.kwargs["json"]["messages"][1]["content"]
        data_uri = next(p for p in content if p["type"] == "image_url")["image_url"]["url"]
        self.assertTrue(data_uri.startswith("data:image/jpeg;base64,"))
        self.assertGreater(len(data_uri), 1000)

    def test_a_student_can_submit_their_own_work(self):
        student = make_user("submitting-student", Role.STUDENT)
        response = self.submit(user=student)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Paper.objects.get().submitted_by, student)


class InvalidRequestTests(SubmitPaperTestCase):
    def test_a_missing_image_is_a_400(self):
        self.client.force_login(self.teacher)
        response = self.client.post(self.url, {"memorandum": self.memorandum.pk})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_request")
        self.assertEqual(Paper.objects.count(), 0)

    def test_a_missing_memorandum_is_a_400(self):
        self.client.force_login(self.teacher)
        response = self.client.post(self.url, {"image": make_upload()})

        self.assertEqual(response.status_code, 400)
        self.assertIn("memorandum", response.json()["detail"])

    def test_a_file_that_is_not_an_image_is_a_400_with_a_readable_reason(self):
        self.client.force_login(self.teacher)
        response = self.client.post(
            self.url,
            {
                "memorandum": self.memorandum.pk,
                "image": make_upload("paper.jpg", b"not an image at all"),
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_image")
        self.assertIn("JPEG or PNG", response.json()["detail"])
        # Nothing was uploaded to storage or recorded.
        self.assertEqual(Paper.objects.count(), 0)


class FailureResponseTests(SubmitPaperTestCase):
    """Failures keep the submission and say what happened."""

    def test_a_rate_limit_comes_back_as_429_with_retry_after(self):
        response = self.submit(
            response=error_response(429, "free tier exhausted", headers={"Retry-After": "60"})
        )
        body = response.json()

        self.assertEqual(response.status_code, 429)
        self.assertEqual(body["error"], Paper.FailureKind.RATE_LIMITED)
        self.assertEqual(body["retry_after"], "60")

    def test_a_rate_limited_submission_is_still_kept(self):
        body = self.submit(response=error_response(429)).json()
        paper = Paper.objects.get(pk=body["paper"]["id"])

        self.assertEqual(paper.status, Paper.Status.FAILED)
        self.assertEqual(paper.failure_kind, Paper.FailureKind.RATE_LIMITED)
        self.assertTrue(paper.image.name)

    def test_missing_credit_comes_back_as_402(self):
        response = self.submit(response=error_response(402, "insufficient credit"))

        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.json()["error"], Paper.FailureKind.NO_CREDIT)

    def test_a_retired_model_slug_comes_back_as_503(self):
        response = self.submit(response=error_response(404, "no such model"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"], Paper.FailureKind.MODEL_UNAVAILABLE)

    def test_an_unparseable_reply_comes_back_as_422(self):
        self.client.force_login(self.teacher)
        with patch("marking.openrouter.requests.post") as post:
            post.return_value = completion_response("I cannot read this image.")
            response = self.client.post(
                self.url, {"memorandum": self.memorandum.pk, "image": make_upload()}
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"], Paper.FailureKind.INVALID_RESPONSE)
        self.assertEqual(Paper.objects.get().status, Paper.Status.FAILED)

    def test_a_failure_response_still_reports_the_paper(self):
        body = self.submit(response=error_response(429)).json()

        self.assertIn("paper", body)
        self.assertEqual(body["paper"]["status"], Paper.Status.FAILED)
        self.assertIn("detail", body)
