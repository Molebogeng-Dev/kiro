"""Tests for the plain-language summary generator.

The behaviour that matters: the AI is used when it works, a templated message is
used when it does not, and either way a non-empty, sendable string comes back.
``generate_summary`` must never raise — a summary failure must never be the
reason a parent hears nothing.

The OpenRouter call is mocked at ``complete_text``; no HTTP happens.
"""

from unittest.mock import patch

from django.test import TestCase

from marking.openrouter import Completion, RateLimited, ServiceUnavailable
from notifications.summary import generate_summary

from .support import make_marked_paper, make_student


def _completion(text):
    return Completion(content=text, model="test-model", usage={})


class AISummaryTests(TestCase):
    def setUp(self):
        self.student = make_student("summary-student", first_name="Thandi")
        self.paper = make_marked_paper(self.student)

    def test_a_working_ai_call_produces_the_message(self):
        with patch(
            "notifications.summary.OpenRouterClient.complete_text",
            return_value=_completion("  Thandi did well on her maths test.  "),
        ):
            message = generate_summary(self.paper)

        # Returned trimmed and verbatim from the model.
        self.assertEqual(message, "Thandi did well on her maths test.")

    def test_the_prompt_carries_the_score_subject_and_feedback(self):
        with patch(
            "notifications.summary.OpenRouterClient.complete_text",
            return_value=_completion("A message."),
        ) as complete_text:
            generate_summary(self.paper)

        prompt = complete_text.call_args.kwargs["user_prompt"]
        self.assertIn("Thandi", prompt)
        self.assertIn("Mathematics", prompt)
        self.assertIn("7", prompt)  # marks awarded
        self.assertIn("10", prompt)  # marks available
        self.assertIn("show the units", prompt)  # per-question feedback

    def test_the_system_prompt_asks_for_plain_parent_language(self):
        with patch(
            "notifications.summary.OpenRouterClient.complete_text",
            return_value=_completion("A message."),
        ) as complete_text:
            generate_summary(self.paper)

        system_prompt = complete_text.call_args.kwargs["system_prompt"]
        self.assertIn("parent", system_prompt.lower())


class FallbackSummaryTests(TestCase):
    """When the AI call fails, a message built from the score alone is used."""

    def setUp(self):
        self.student = make_student("fallback-student", first_name="Sipho")
        self.paper = make_marked_paper(self.student)

    def test_a_rate_limit_falls_back_to_a_templated_message(self):
        with patch(
            "notifications.summary.OpenRouterClient.complete_text",
            side_effect=RateLimited("no quota"),
        ):
            message = generate_summary(self.paper)

        self.assertIn("Sipho", message)
        self.assertIn("7", message)
        self.assertIn("10", message)
        self.assertIn("70.0%", message)

    def test_a_service_outage_falls_back_too(self):
        with patch(
            "notifications.summary.OpenRouterClient.complete_text",
            side_effect=ServiceUnavailable("provider down"),
        ):
            message = generate_summary(self.paper)

        self.assertTrue(message)
        self.assertIn("Sipho", message)

    def test_an_unexpected_error_still_falls_back_and_never_raises(self):
        # A bug the summary code did not anticipate must not break notifying.
        with patch(
            "notifications.summary.OpenRouterClient.complete_text",
            side_effect=ValueError("something unforeseen"),
        ):
            message = generate_summary(self.paper)

        self.assertTrue(message)
        self.assertIn("Sipho", message)

    def test_a_marked_paper_without_a_result_still_yields_a_message(self):
        paper = make_marked_paper(
            make_student("no-result-student", first_name="Lerato"),
            with_result=False,
        )
        # No AI call should even be attempted; there's nothing to summarise.
        with patch(
            "notifications.summary.OpenRouterClient.complete_text"
        ) as complete_text:
            message = generate_summary(paper)

        complete_text.assert_not_called()
        self.assertIn("Lerato", message)
        self.assertTrue(message)

    def test_the_fallback_names_the_subject(self):
        with patch(
            "notifications.summary.OpenRouterClient.complete_text",
            side_effect=RateLimited("no quota"),
        ):
            message = generate_summary(self.paper)

        self.assertIn("Mathematics", message)
