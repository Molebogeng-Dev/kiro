"""Turning a marked paper into a few plain sentences for a parent.

The audience is a parent reading a WhatsApp message on their phone, not a
teacher reading a mark sheet. So the summary avoids jargon, avoids per-question
tables, and says the three things a parent actually wants: what the work was,
how it went, and where to focus next.

The one hard rule here mirrors the sprint's intent: this function never raises.
A summary is a nice-to-have wrapped around a must-have (the notification going
out at all). If the AI call fails for any reason — rate limit, timeout, an empty
reply, or something we did not foresee — we fall back to a message built from the
score alone. A parent gets a real, sendable sentence either way.
"""

import logging

from marking.openrouter import OpenRouterClient, OpenRouterError

logger = logging.getLogger(__name__)

# How many characters of the WhatsApp message the model may produce. Kept short
# on purpose: this is a phone notification, not a report.
_MAX_SUMMARY_TOKENS = 300

_SYSTEM_PROMPT = (
    "You write short, warm WhatsApp messages to a parent about their child's "
    "marked schoolwork. Write in plain language a parent understands at a "
    "glance, with no jargon and no mark-by-mark breakdown. Two or three short "
    "sentences: what the work was, how it went overall, and one encouraging "
    "note on where to focus next. Do not invent details you were not given."
)


def generate_summary(paper) -> str:
    """A parent-friendly message about ``paper``'s result.

    Tries the text-only OpenRouter call first, and falls back to a templated
    message built from the score if anything at all goes wrong. Always returns a
    non-empty string ready to send.
    """
    result = getattr(paper, "result", None)
    if result is None:
        # Nothing to summarise. Should not happen on a marked paper, but the
        # notification must still have a body, so give the safest generic line.
        return _fallback_summary(paper, result)

    try:
        return _ai_summary(paper, result)
    except OpenRouterError as exc:
        logger.warning(
            "Summary generation for paper %s fell back to a template: %s",
            paper.pk,
            exc,
        )
        return _fallback_summary(paper, result)
    except Exception:  # noqa: BLE001 - a summary must never break notifying.
        logger.exception(
            "Unexpected error summarising paper %s; using the templated fallback.",
            paper.pk,
        )
        return _fallback_summary(paper, result)


def _ai_summary(paper, result) -> str:
    client = OpenRouterClient.from_settings()
    completion = client.complete_text(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=_build_prompt(paper, result),
    )
    return completion.content.strip()


def _build_prompt(paper, result) -> str:
    """Everything the model needs, and nothing it can leak or misuse."""
    child = _child_name(paper)
    subject = paper.memorandum.subject or paper.memorandum.title
    lines = [
        f"Child: {child}",
        f"Subject: {subject}",
        f"Score: {result.marks_awarded} out of {result.marks_available}",
    ]
    percentage = result.percentage
    if percentage is not None:
        lines.append(f"Percentage: {percentage}%")
    if result.summary:
        lines.append(f"Overall teacher-style summary: {result.summary}")

    questions = list(result.questions.all())
    if questions:
        lines.append("Per-question feedback:")
        for question in questions:
            lines.append(
                f"- Q{question.number} "
                f"({question.marks_awarded}/{question.marks_available}): "
                f"{question.feedback}"
            )

    lines.append(
        "\nWrite the parent message now. Keep it to two or three short sentences."
    )
    return "\n".join(lines)


def _fallback_summary(paper, result) -> str:
    """A sendable message built from the score alone, no AI involved."""
    child = _child_name(paper)
    subject = None
    if paper.memorandum_id:
        subject = paper.memorandum.subject or paper.memorandum.title

    where = f" in {subject}" if subject else ""

    if result is None:
        return (
            f"{child}'s work{where} has been marked on iSgela. "
            f"Log in to see the full result."
        )

    percentage = result.percentage
    score = f"{result.marks_awarded} out of {result.marks_available}"
    if percentage is not None:
        return (
            f"{child}'s work{where} has been marked: {score} ({percentage}%). "
            f"Log in to iSgela to see the detailed feedback."
        )
    return (
        f"{child}'s work{where} has been marked: {score}. "
        f"Log in to iSgela to see the detailed feedback."
    )


def _child_name(paper) -> str:
    """The child's display name, falling back gracefully."""
    student = paper.student
    if student is None:
        return "Your child"
    return student.get_full_name() or student.first_name or student.username
