"""Turning whatever the model said into a marking result we can trust.

An open-weight model on a free endpoint is noticeably less obedient about output
format than a top commercial model. In practice the drift is mundane and
repetitive: the JSON arrives wrapped in ```json fences, or with a sentence of
preamble before it, or with ``awarded`` instead of ``marks_awarded``. None of
that is worth failing a submission over, so this module normalises it.

What it will not do is guess about marks. A response that is missing a
per-question breakdown, or that awards more marks than a question is worth, is
rejected so the caller can retry rather than persisting a number a teacher might
rely on. Arithmetic is recomputed from the per-question marks rather than trusted
from the model's own total, because addition is the one part of this we can do
perfectly ourselves.
"""

import json
import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

# ```json ... ``` or ``` ... ```, with optional surrounding whitespace.
CODE_FENCE_RE = re.compile(
    r"```[ \t]*(?:json|JSON)?[ \t]*\r?\n?(?P<body>.*?)\r?\n?[ \t]*```",
    re.DOTALL,
)

# Key spellings seen in the wild, mapped to the ones we use.
AWARDED_KEYS = ("marks_awarded", "awarded", "mark_awarded", "marks_given", "score")
AVAILABLE_KEYS = (
    "marks_available",
    "available",
    "marks_total",
    "total_marks",
    "out_of",
    "max_marks",
    "marks",
)
NUMBER_KEYS = ("number", "question", "question_number", "q", "no")
FEEDBACK_KEYS = ("feedback", "comment", "explanation", "reason", "why")
QUESTION_LIST_KEYS = ("questions", "results", "per_question", "breakdown")
OVERALL_KEYS = ("overall", "total", "summary_marks", "overall_score")
SUMMARY_KEYS = ("summary", "overall_feedback", "comment", "general_feedback")

TWO_PLACES = Decimal("0.01")


class MarkingResponseError(ValueError):
    """The reply could not be turned into a result. Worth one retry."""


@dataclass(frozen=True)
class ParsedQuestion:
    number: str
    marks_awarded: Decimal
    marks_available: Decimal
    feedback: str


@dataclass(frozen=True)
class ParsedMarking:
    marks_awarded: Decimal
    marks_available: Decimal
    summary: str
    questions: tuple

    @property
    def percentage(self):
        if not self.marks_available:
            return None
        return round(float(self.marks_awarded) / float(self.marks_available) * 100, 1)


def strip_code_fences(text: str) -> str:
    """Remove a markdown code fence around the payload, if there is one."""
    match = CODE_FENCE_RE.search(text)
    if match:
        return match.group("body").strip()
    return text.strip()


def extract_json_object(text: str) -> str:
    """Pull the outermost JSON object out of a reply that has prose around it.

    Brace matching rather than a regex, and string-aware, so a ``}`` inside
    feedback text does not end the object early.
    """
    start = text.find("{")
    if start == -1:
        raise MarkingResponseError("The reply contained no JSON object.")

    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(text)):
        character = text[index]

        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue

        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    raise MarkingResponseError("The reply contained an unterminated JSON object.")


def parse_marking_response(text: str) -> ParsedMarking:
    """Parse a model reply into a validated ``ParsedMarking``."""
    if not text or not text.strip():
        raise MarkingResponseError("The model returned an empty reply.")

    payload = strip_code_fences(text)

    try:
        data = json.loads(payload)
    except ValueError:
        # Prose around the JSON is the most common drift after code fences.
        try:
            data = json.loads(extract_json_object(payload))
        except ValueError as exc:
            raise MarkingResponseError(f"The reply was not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise MarkingResponseError(
            f"Expected a JSON object, got {type(data).__name__}."
        )

    questions = _parse_questions(data)

    # Recomputed rather than taken from the model. If it also reported a total,
    # a mismatch is logged: it is a useful signal about how well the model is
    # following instructions, but the per-question marks are what a teacher
    # would check, so they win.
    awarded = _quantize(sum(question.marks_awarded for question in questions))
    available = _quantize(sum(question.marks_available for question in questions))
    _warn_on_total_mismatch(data, awarded, available)

    return ParsedMarking(
        marks_awarded=awarded,
        marks_available=available,
        summary=_first_string(data, SUMMARY_KEYS),
        questions=tuple(questions),
    )


def _parse_questions(data: dict) -> list:
    raw_questions = _first_present(data, QUESTION_LIST_KEYS)

    if raw_questions is None:
        raise MarkingResponseError(
            "The reply had no per-question breakdown; expected a 'questions' array."
        )
    if not isinstance(raw_questions, list) or not raw_questions:
        raise MarkingResponseError("'questions' must be a non-empty array.")

    questions = []
    for position, entry in enumerate(raw_questions, start=1):
        if not isinstance(entry, dict):
            raise MarkingResponseError(
                f"Question {position} was {type(entry).__name__}, expected an object."
            )
        questions.append(_parse_question(entry, position))

    return questions


def _parse_question(entry: dict, position: int) -> ParsedQuestion:
    number = _first_string(entry, NUMBER_KEYS)
    if not number:
        number = str(position)

    awarded = _decimal_from(entry, AWARDED_KEYS, position, "marks_awarded")
    available = _decimal_from(entry, AVAILABLE_KEYS, position, "marks_available")

    if awarded < 0 or available < 0:
        raise MarkingResponseError(f"Question {number} has negative marks.")
    if awarded > available:
        # Not clamped: a model that awards 5 out of 3 has misread the memo, and
        # quietly capping it would hide that behind a plausible-looking mark.
        raise MarkingResponseError(
            f"Question {number} awards {awarded} of {available} marks available."
        )

    feedback = _first_string(entry, FEEDBACK_KEYS)
    if awarded < available and not feedback:
        # The whole product promise is explaining *why* a mark was lost.
        # A bare number is not a result worth storing.
        raise MarkingResponseError(
            f"Question {number} lost marks but came back with no feedback."
        )

    return ParsedQuestion(
        number=str(number)[:20],
        marks_awarded=_quantize(awarded),
        marks_available=_quantize(available),
        feedback=feedback,
    )


def _first_present(data: dict, keys):
    lowered = {str(key).lower(): value for key, value in data.items()}
    for key in keys:
        if key in lowered and lowered[key] is not None:
            return lowered[key]
    return None


def _first_string(data: dict, keys) -> str:
    value = _first_present(data, keys)
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    return ""


def _decimal_from(entry: dict, keys, position, label) -> Decimal:
    value = _first_present(entry, keys)
    if value is None:
        raise MarkingResponseError(f"Question {position} is missing {label}.")

    if isinstance(value, bool):
        raise MarkingResponseError(f"Question {position} has a boolean {label}.")

    if isinstance(value, str):
        value = value.strip().replace(",", ".")

    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MarkingResponseError(
            f"Question {position} has a non-numeric {label}: {value!r}."
        ) from exc


def _quantize(value) -> Decimal:
    return Decimal(value).quantize(TWO_PLACES)


def _warn_on_total_mismatch(data: dict, awarded: Decimal, available: Decimal):
    overall = _first_present(data, OVERALL_KEYS)
    if not isinstance(overall, dict):
        return

    try:
        reported_awarded = _decimal_from(overall, AWARDED_KEYS, 0, "marks_awarded")
        reported_available = _decimal_from(overall, AVAILABLE_KEYS, 0, "marks_available")
    except MarkingResponseError:
        return

    if _quantize(reported_awarded) != awarded or _quantize(reported_available) != available:
        logger.warning(
            "Model total %s/%s disagreed with the sum of its questions %s/%s; "
            "using the per-question sum.",
            reported_awarded,
            reported_available,
            awarded,
            available,
        )
