"""Tests for the marking engine.

The recurring assertion in this file: whatever happens, the Paper row is still
there afterwards with an honest status on it. A learner who submitted their
homework should never have it vanish because a provider was busy.
"""

import json
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from marking.engine import mark_paper
from marking.models import MarkingResult, Paper, QuestionResult
from marking.openrouter import (
    InsufficientCredit,
    ModelUnavailable,
    RateLimited,
    ServiceUnavailable,
)
from marking.parsing import MarkingResponseError

from .support import (
    completion_response,
    error_response,
    make_image_bytes,
    make_memorandum,
    make_upload,
    make_user,
    valid_marking_json,
)


class EngineTestCase(TestCase):
    def setUp(self):
        self.memorandum = make_memorandum()
        self.teacher = make_user("engine-teacher")
        self.paper = Paper.objects.create(
            memorandum=self.memorandum,
            submitted_by=self.teacher,
            image=make_upload(),
        )

    def mark(self, *responses):
        """Run the engine with a scripted sequence of HTTP responses."""
        with patch("marking.openrouter.requests.post") as post:
            post.side_effect = list(responses)
            try:
                return mark_paper(self.paper, image_bytes=b"jpeg-bytes")
            finally:
                self.post = post


class SuccessfulMarkingTests(EngineTestCase):
    def test_a_good_reply_is_stored_as_a_result(self):
        result = self.mark(completion_response(valid_marking_json()))

        self.assertEqual(result.marks_awarded, Decimal("4.00"))
        self.assertEqual(result.marks_available, Decimal("6.00"))
        self.assertEqual(result.percentage, 66.7)
        self.assertIn("arithmetic slip", result.summary)
        self.assertEqual(result.model_used, "qwen/qwen2.5-vl-72b-instruct")

    def test_the_paper_is_marked_and_carries_no_failure(self):
        self.mark(completion_response(valid_marking_json()))
        self.paper.refresh_from_db()

        self.assertEqual(self.paper.status, Paper.Status.MARKED)
        self.assertEqual(self.paper.failure_kind, "")
        self.assertEqual(self.paper.failure_detail, "")

    def test_every_question_is_stored_with_its_feedback(self):
        result = self.mark(completion_response(valid_marking_json()))
        questions = list(result.questions.all())

        self.assertEqual([question.number for question in questions], ["1.1", "1.2"])
        self.assertEqual(questions[1].marks_awarded, Decimal("2.00"))
        self.assertEqual(questions[1].marks_lost, Decimal("2.00"))
        self.assertIn("six times table", questions[1].feedback)
        self.assertTrue(questions[0].is_full_marks)

    def test_the_raw_reply_is_kept_for_debugging(self):
        result = self.mark(completion_response(valid_marking_json()))
        self.assertIn("marks_awarded", result.raw_response)

    def test_a_fenced_reply_still_produces_a_result(self):
        result = self.mark(completion_response(f"```json\n{valid_marking_json()}\n```"))
        self.assertEqual(result.marks_awarded, Decimal("4.00"))

    def test_remarking_replaces_the_previous_result(self):
        self.mark(completion_response(valid_marking_json()))
        self.mark(completion_response(valid_marking_json()))

        self.assertEqual(MarkingResult.objects.filter(paper=self.paper).count(), 1)
        self.assertEqual(QuestionResult.objects.count(), 2)

    def test_the_image_is_read_from_storage_when_not_supplied(self):
        """Sprint 3 will re-mark stored papers, with no upload in hand."""
        with patch("marking.openrouter.requests.post") as post:
            post.return_value = completion_response(valid_marking_json())
            mark_paper(self.paper)

        content = post.call_args.kwargs["json"]["messages"][1]["content"]
        image_part = next(part for part in content if part["type"] == "image_url")
        self.assertTrue(len(image_part["image_url"]["url"]) > 100)


class MalformedReplyTests(EngineTestCase):
    """One retry, with the problem described back to the model."""

    def test_a_malformed_reply_is_retried_and_can_succeed(self):
        result = self.mark(
            completion_response("I could not do this in JSON, sorry."),
            completion_response(valid_marking_json()),
        )

        self.assertEqual(self.post.call_count, 2)
        self.assertEqual(result.marks_awarded, Decimal("4.00"))
        self.paper.refresh_from_db()
        self.assertEqual(self.paper.status, Paper.Status.MARKED)

    def test_the_retry_tells_the_model_what_was_wrong(self):
        self.mark(
            completion_response("not json at all"),
            completion_response(valid_marking_json()),
        )

        retry_prompt = self.post.call_args_list[1].kwargs["json"]["messages"][1]["content"][0]["text"]
        self.assertIn("previous reply could not be used", retry_prompt)
        self.assertIn("Do not wrap it in", retry_prompt)

    def test_two_malformed_replies_fail_the_paper_without_losing_it(self):
        with self.assertRaises(MarkingResponseError):
            self.mark(
                completion_response("nope"),
                completion_response("still nope"),
            )

        self.assertEqual(self.post.call_count, 2)
        self.paper.refresh_from_db()
        self.assertEqual(self.paper.status, Paper.Status.FAILED)
        self.assertEqual(self.paper.failure_kind, Paper.FailureKind.INVALID_RESPONSE)
        self.assertTrue(self.paper.failure_detail)
        self.assertTrue(Paper.objects.filter(pk=self.paper.pk).exists())

    def test_no_partial_result_is_left_behind_when_parsing_fails(self):
        with self.assertRaises(MarkingResponseError):
            self.mark(completion_response("nope"), completion_response("nope"))

        self.assertFalse(MarkingResult.objects.filter(paper=self.paper).exists())
        self.assertEqual(QuestionResult.objects.count(), 0)

    def test_a_reply_awarding_impossible_marks_is_not_stored(self):
        impossible = json.dumps(
            {
                "questions": [
                    {
                        "number": "1.1",
                        "marks_awarded": 10,
                        "marks_available": 2,
                        "feedback": "Perfect.",
                    }
                ]
            }
        )

        with self.assertRaises(MarkingResponseError):
            self.mark(completion_response(impossible), completion_response(impossible))

        self.paper.refresh_from_db()
        self.assertEqual(self.paper.status, Paper.Status.FAILED)


class TransportFailureTests(EngineTestCase):
    """Each provider failure is recorded as its own kind, and not retried blindly."""

    def assert_failure(self, response, expected_exception, expected_kind):
        with self.assertRaises(expected_exception):
            self.mark(response)

        self.paper.refresh_from_db()
        self.assertEqual(self.paper.status, Paper.Status.FAILED)
        self.assertEqual(self.paper.failure_kind, expected_kind)
        self.assertTrue(self.paper.failure_detail)

    def test_a_rate_limit_is_recorded_as_a_rate_limit(self):
        self.assert_failure(
            error_response(429, "free tier exhausted"),
            RateLimited,
            Paper.FailureKind.RATE_LIMITED,
        )

    def test_missing_credit_is_recorded_distinctly(self):
        self.assert_failure(
            error_response(402, "add credit"),
            InsufficientCredit,
            Paper.FailureKind.NO_CREDIT,
        )

    def test_a_retired_model_slug_is_recorded_distinctly(self):
        self.assert_failure(
            error_response(404, "unknown model"),
            ModelUnavailable,
            Paper.FailureKind.MODEL_UNAVAILABLE,
        )

    def test_a_provider_outage_is_recorded_as_a_service_error(self):
        with patch("marking.openrouter.time.sleep"):
            with self.assertRaises(ServiceUnavailable):
                self.mark(error_response(503), error_response(503))

        self.paper.refresh_from_db()
        self.assertEqual(self.paper.failure_kind, Paper.FailureKind.SERVICE_ERROR)

    def test_a_transport_failure_is_not_retried_as_if_it_were_malformed(self):
        """A 429 should cost one call, not two."""
        with self.assertRaises(RateLimited):
            self.mark(error_response(429))

        self.assertEqual(self.post.call_count, 1)


class PromptContentTests(EngineTestCase):
    def test_the_memorandum_is_sent_to_the_model(self):
        self.mark(completion_response(valid_marking_json()))

        messages = self.post.call_args.kwargs["json"]["messages"]
        prompt = messages[1]["content"][0]["text"]

        self.assertIn("Grade 4 Mathematics Test 1", prompt)
        self.assertIn("Expected: 48", prompt)
        self.assertIn("Mathematics", prompt)
        self.assertIn("Total marks available: 6", prompt)

    def test_the_system_prompt_asks_for_reasons_not_just_marks(self):
        self.mark(completion_response(valid_marking_json()))

        system_prompt = self.post.call_args.kwargs["json"]["messages"][0]["content"]

        self.assertIn("WHY", system_prompt)
        self.assertIn("parent", system_prompt)


class ImageHandlingTests(TestCase):
    def test_a_paper_keeps_its_image_in_the_configured_storage(self):
        """Storage is swapped for an in-memory backend under test, never Supabase."""
        paper = Paper.objects.create(
            memorandum=make_memorandum(),
            submitted_by=make_user("storage-teacher"),
            image=make_upload(content=make_image_bytes(size=(400, 300))),
        )

        self.assertTrue(paper.image.name.startswith("papers/"))
        self.assertTrue(paper.image.name.endswith(".jpg"))
        self.assertGreater(paper.image.size, 0)
