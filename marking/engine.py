"""Marking a paper end to end.

The one rule this module exists to enforce: a submission is never lost. Whatever
goes wrong, the Paper row survives with a status and a reason attached, because a
learner who photographed their homework has done their part and should not have
that disappear because a provider was rate limiting us.
"""

import logging

from django.db import transaction

from .models import MarkingResult, Paper, QuestionResult
from .openrouter import (
    InsufficientCredit,
    ModelUnavailable,
    OpenRouterClient,
    OpenRouterError,
    OpenRouterNotConfigured,
    RateLimited,
)
from .parsing import MarkingResponseError, parse_marking_response
from .prompts import SYSTEM_PROMPT, build_retry_prompt, build_user_prompt

logger = logging.getLogger(__name__)

# One retry on a malformed reply. A second attempt with the error described back
# to the model recovers most format drift; a third rarely adds anything but
# latency and quota.
PARSE_ATTEMPTS = 2

# Which failure kind to record for each transport-level exception.
FAILURE_KINDS = {
    RateLimited: Paper.FailureKind.RATE_LIMITED,
    InsufficientCredit: Paper.FailureKind.NO_CREDIT,
    ModelUnavailable: Paper.FailureKind.MODEL_UNAVAILABLE,
    OpenRouterNotConfigured: Paper.FailureKind.SERVICE_ERROR,
}


def mark_paper(paper, *, image_bytes=None, client=None) -> MarkingResult:
    """Mark ``paper`` against its memorandum and persist the result.

    ``image_bytes`` lets a caller that has just processed an upload pass the
    bytes straight through, avoiding a pointless download of something it
    already has in memory.

    On success the paper is ``marked`` and its ``MarkingResult`` is returned. On
    failure the paper is ``failed`` with a ``failure_kind``, and the underlying
    exception is re-raised for the caller to turn into a response.
    """
    client = client or OpenRouterClient.from_settings()
    memorandum = paper.memorandum

    if image_bytes is None:
        image_bytes = _read_image(paper)

    system_prompt = SYSTEM_PROMPT
    user_prompt = build_user_prompt(memorandum)
    last_parse_error = None

    for attempt in range(1, PARSE_ATTEMPTS + 1):
        try:
            completion = client.complete_with_image(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                image_bytes=image_bytes,
            )
        except OpenRouterError as exc:
            kind = FAILURE_KINDS.get(type(exc), Paper.FailureKind.SERVICE_ERROR)
            logger.warning("Marking paper %s failed (%s): %s", paper.pk, kind, exc)
            paper.record_failure(kind, exc)
            raise

        try:
            parsed = parse_marking_response(completion.content)
        except MarkingResponseError as exc:
            last_parse_error = exc
            logger.warning(
                "Paper %s: unparseable reply from %s on attempt %s of %s: %s",
                paper.pk,
                completion.model,
                attempt,
                PARSE_ATTEMPTS,
                exc,
            )
            # Tell the model exactly what was wrong with the last attempt.
            user_prompt = build_retry_prompt(memorandum, exc)
            continue

        return _persist(paper, parsed, completion)

    paper.record_failure(Paper.FailureKind.INVALID_RESPONSE, last_parse_error)
    raise last_parse_error


def _read_image(paper):
    paper.image.open("rb")
    try:
        return paper.image.read()
    finally:
        paper.image.close()


@transaction.atomic
def _persist(paper, parsed, completion) -> MarkingResult:
    """Write the result, replacing any previous attempt at the same paper."""
    MarkingResult.objects.filter(paper=paper).delete()

    result = MarkingResult.objects.create(
        paper=paper,
        marks_awarded=parsed.marks_awarded,
        marks_available=parsed.marks_available,
        summary=parsed.summary,
        model_used=completion.model,
        raw_response=completion.content,
    )

    QuestionResult.objects.bulk_create(
        [
            QuestionResult(
                result=result,
                number=question.number,
                marks_awarded=question.marks_awarded,
                marks_available=question.marks_available,
                feedback=question.feedback,
                position=position,
            )
            for position, question in enumerate(parsed.questions, start=1)
        ]
    )

    paper.status = Paper.Status.MARKED
    paper.failure_kind = ""
    paper.failure_detail = ""
    paper.save(update_fields=["status", "failure_kind", "failure_detail", "updated_at"])

    return result
