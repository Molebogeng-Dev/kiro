"""Tests for parsing the model's reply.

These are the tests that matter most for credibility. Everything a free-tier
open-weight model does to a JSON schema shows up here, and the boundary between
"tolerate this" and "refuse this" is the boundary between helpful normalisation
and inventing marks.
"""

import json
from decimal import Decimal

from django.test import SimpleTestCase

from marking.parsing import (
    MarkingResponseError,
    extract_json_object,
    parse_marking_response,
    strip_code_fences,
)

from .support import VALID_MARKING, valid_marking_json


class CodeFenceTests(SimpleTestCase):
    """Wrapping JSON in markdown is the single most common drift."""

    def test_strips_a_json_labelled_fence(self):
        wrapped = f"```json\n{valid_marking_json()}\n```"
        self.assertEqual(strip_code_fences(wrapped), valid_marking_json())

    def test_strips_an_unlabelled_fence(self):
        wrapped = f"```\n{valid_marking_json()}\n```"
        self.assertEqual(strip_code_fences(wrapped), valid_marking_json())

    def test_leaves_unfenced_text_alone(self):
        self.assertEqual(strip_code_fences(valid_marking_json()), valid_marking_json())

    def test_a_fenced_reply_parses_end_to_end(self):
        parsed = parse_marking_response(f"```json\n{valid_marking_json()}\n```")
        self.assertEqual(parsed.marks_awarded, Decimal("4.00"))


class SurroundingProseTests(SimpleTestCase):
    def test_a_reply_with_a_preamble_still_parses(self):
        reply = (
            "Sure! I have marked the paper against the memorandum. "
            f"Here are the results:\n\n{valid_marking_json()}\n\n"
            "Let me know if you would like anything explained."
        )
        parsed = parse_marking_response(reply)

        self.assertEqual(parsed.marks_awarded, Decimal("4.00"))
        self.assertEqual(len(parsed.questions), 2)

    def test_braces_inside_feedback_do_not_end_the_object_early(self):
        payload = {
            "questions": [
                {
                    "number": "1",
                    "marks_awarded": 0,
                    "marks_available": 2,
                    "feedback": "The learner wrote {x} instead of solving for x.",
                }
            ]
        }
        reply = f"Here you go:\n{json.dumps(payload)}"
        parsed = parse_marking_response(reply)

        self.assertIn("{x}", parsed.questions[0].feedback)

    def test_an_unterminated_object_is_rejected(self):
        with self.assertRaises(MarkingResponseError):
            extract_json_object('{"questions": [')


class SchemaDriftTests(SimpleTestCase):
    """Alternative key spellings are tolerated; missing marks are not."""

    def test_alternative_key_names_are_understood(self):
        reply = json.dumps(
            {
                "results": [
                    {
                        "question": "1.1",
                        "awarded": 1,
                        "out_of": 2,
                        "comment": "Half the working is missing.",
                    }
                ]
            }
        )
        parsed = parse_marking_response(reply)

        self.assertEqual(parsed.questions[0].number, "1.1")
        self.assertEqual(parsed.questions[0].marks_awarded, Decimal("1.00"))
        self.assertEqual(parsed.questions[0].marks_available, Decimal("2.00"))
        self.assertEqual(parsed.questions[0].feedback, "Half the working is missing.")

    def test_numeric_strings_and_halves_are_accepted(self):
        reply = json.dumps(
            {
                "questions": [
                    {
                        "number": "1",
                        "marks_awarded": "1.5",
                        "marks_available": "2",
                        "feedback": "Rounded too early.",
                    }
                ]
            }
        )
        parsed = parse_marking_response(reply)
        self.assertEqual(parsed.questions[0].marks_awarded, Decimal("1.50"))

    def test_a_missing_question_number_falls_back_to_position(self):
        reply = json.dumps(
            {"questions": [{"marks_awarded": 2, "marks_available": 2}]}
        )
        self.assertEqual(parse_marking_response(reply).questions[0].number, "1")

    def test_a_reply_with_no_question_breakdown_is_rejected(self):
        """An overall mark with no working is not a marking result."""
        reply = json.dumps({"overall": {"marks_awarded": 4, "marks_available": 6}})
        with self.assertRaises(MarkingResponseError) as caught:
            parse_marking_response(reply)
        self.assertIn("per-question", str(caught.exception))

    def test_an_empty_question_array_is_rejected(self):
        with self.assertRaises(MarkingResponseError):
            parse_marking_response(json.dumps({"questions": []}))

    def test_a_question_missing_its_marks_is_rejected(self):
        reply = json.dumps(
            {"questions": [{"number": "1", "feedback": "Good work."}]}
        )
        with self.assertRaises(MarkingResponseError):
            parse_marking_response(reply)

    def test_non_numeric_marks_are_rejected(self):
        reply = json.dumps(
            {
                "questions": [
                    {"number": "1", "marks_awarded": "most of it", "marks_available": 2}
                ]
            }
        )
        with self.assertRaises(MarkingResponseError):
            parse_marking_response(reply)

    def test_awarding_more_than_available_is_rejected_not_clamped(self):
        """Silently capping 5/3 to 3/3 would hide a misread memorandum."""
        reply = json.dumps(
            {
                "questions": [
                    {
                        "number": "1",
                        "marks_awarded": 5,
                        "marks_available": 3,
                        "feedback": "Excellent.",
                    }
                ]
            }
        )
        with self.assertRaises(MarkingResponseError) as caught:
            parse_marking_response(reply)
        self.assertIn("awards", str(caught.exception))

    def test_negative_marks_are_rejected(self):
        reply = json.dumps(
            {
                "questions": [
                    {
                        "number": "1",
                        "marks_awarded": -1,
                        "marks_available": 2,
                        "feedback": "Wrong.",
                    }
                ]
            }
        )
        with self.assertRaises(MarkingResponseError):
            parse_marking_response(reply)

    def test_lost_marks_without_feedback_are_rejected(self):
        """Explaining why a mark was lost is the product, not a nicety."""
        reply = json.dumps(
            {"questions": [{"number": "1", "marks_awarded": 0, "marks_available": 2}]}
        )
        with self.assertRaises(MarkingResponseError) as caught:
            parse_marking_response(reply)
        self.assertIn("no feedback", str(caught.exception))

    def test_full_marks_without_feedback_is_allowed(self):
        reply = json.dumps(
            {"questions": [{"number": "1", "marks_awarded": 2, "marks_available": 2}]}
        )
        parsed = parse_marking_response(reply)
        self.assertEqual(parsed.questions[0].feedback, "")

    def test_a_json_array_instead_of_an_object_is_rejected(self):
        with self.assertRaises(MarkingResponseError):
            parse_marking_response(json.dumps([VALID_MARKING]))

    def test_an_empty_reply_is_rejected(self):
        for reply in ("", "   ", "\n"):
            with self.subTest(reply=repr(reply)):
                with self.assertRaises(MarkingResponseError):
                    parse_marking_response(reply)

    def test_prose_with_no_json_at_all_is_rejected(self):
        with self.assertRaises(MarkingResponseError):
            parse_marking_response("I am unable to read this image, sorry.")


class TotalsTests(SimpleTestCase):
    """Totals are computed from the per-question marks, not trusted."""

    def test_totals_are_summed_from_the_questions(self):
        parsed = parse_marking_response(valid_marking_json())

        self.assertEqual(parsed.marks_awarded, Decimal("4.00"))
        self.assertEqual(parsed.marks_available, Decimal("6.00"))
        self.assertEqual(parsed.percentage, 66.7)

    def test_a_wrong_total_from_the_model_is_overridden_by_the_sum(self):
        """Models add up badly. We do not have to."""
        reply = valid_marking_json(
            overall={"marks_awarded": 6, "marks_available": 6}
        )
        with self.assertLogs("marking.parsing", level="WARNING") as logs:
            parsed = parse_marking_response(reply)

        self.assertEqual(parsed.marks_awarded, Decimal("4.00"))
        self.assertIn("disagreed", logs.output[0])

    def test_a_missing_overall_block_is_fine(self):
        payload = dict(VALID_MARKING)
        payload.pop("overall")
        parsed = parse_marking_response(json.dumps(payload))
        self.assertEqual(parsed.marks_awarded, Decimal("4.00"))

    def test_percentage_is_none_when_nothing_was_available(self):
        reply = json.dumps(
            {"questions": [{"number": "1", "marks_awarded": 0, "marks_available": 0}]}
        )
        self.assertIsNone(parse_marking_response(reply).percentage)

    def test_the_summary_is_carried_through(self):
        parsed = parse_marking_response(valid_marking_json())
        self.assertIn("arithmetic slip", parsed.summary)
