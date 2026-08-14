"""Transcribe a photographed document into editable text.

This reuses Sprint 2's image pipeline and OpenRouter client, so "upload a photo"
works the same way whether a teacher is marking a paper or capturing a
memorandum. The two differences from marking are deliberate:

1. The model is asked for plain text, not JSON. Transcription has no marks to
   validate, and forcing a schema onto it would only add a failure mode.
2. The result is never saved on its own. It pre-fills a form the teacher reviews
   and edits before saving. OCR misreads, and a marking guide silently altered
   by a bad transcription is worse than one the teacher typed by hand. The whole
   point of iSgela is trustworthy marking, so the human stays in the loop here.

The source image is not stored: it is a means of getting text, nothing more.
"""

import enum
import logging

from django.conf import settings

from core.images import process_upload

from .openrouter import (
    InsufficientCredit,
    ModelUnavailable,
    OpenRouterClient,
    OpenRouterError,
    OpenRouterNotConfigured,
    RateLimited,
)
from .parsing import strip_code_fences
from .prompts import (
    ASSIGNMENT_TRANSCRIPTION_USER,
    MEMORANDUM_TRANSCRIPTION_USER,
    TRANSCRIPTION_SYSTEM,
)

logger = logging.getLogger(__name__)


class TranscriptionKind(enum.Enum):
    MEMORANDUM = "memorandum"
    ASSIGNMENT = "assignment"


_USER_PROMPTS = {
    TranscriptionKind.MEMORANDUM: MEMORANDUM_TRANSCRIPTION_USER,
    TranscriptionKind.ASSIGNMENT: ASSIGNMENT_TRANSCRIPTION_USER,
}


class TranscriptionError(Exception):
    """Transcription could not be completed.

    The message is written for a teacher and always ends by pointing them at
    typing the text in instead, because that fallback is always available.
    """


# Every message ends the same way on purpose: whatever went wrong, the teacher
# is never stuck, because the form underneath still takes typed input.
_FAILURE_MESSAGES = {
    RateLimited: (
        "The reading service is busy right now. Wait a minute and try again, "
        "or type it in below."
    ),
    InsufficientCredit: (
        "The reading service has run out of credit. You can type it in below "
        "for now."
    ),
    ModelUnavailable: (
        "The reading service is unavailable at the moment. You can type it in below."
    ),
    OpenRouterNotConfigured: (
        "The reading service is not set up yet. You can type it in below."
    ),
}
_DEFAULT_FAILURE = (
    "We couldn't read the photo just now. Please try again, or type it in below."
)
_EMPTY_RESULT = (
    "We couldn't make out any text in that photo. Try a clearer, straighter "
    "photo in good light, or type it in below."
)


def transcribe_upload(uploaded_file, *, kind, client=None) -> str:
    """Validate and compress an upload, then transcribe it to plain text.

    Raises ``ImageValidationError`` (from ``core.images``) when the file is not
    a usable image, and ``TranscriptionError`` when the model cannot be reached
    or returns nothing usable. Both carry teacher-facing messages.
    """
    processed = process_upload(
        uploaded_file,
        max_dimension=settings.MARKING_IMAGE_MAX_DIMENSION,
        jpeg_quality=settings.MARKING_IMAGE_JPEG_QUALITY,
        max_bytes=settings.MARKING_MAX_UPLOAD_BYTES,
    )
    return transcribe_document(image_bytes=processed.data, kind=kind, client=client)


def transcribe_document(*, image_bytes, kind, client=None) -> str:
    """Send image bytes to the vision model and return the transcribed text."""
    client = client or OpenRouterClient.from_settings()

    try:
        completion = client.complete_with_image(
            system_prompt=TRANSCRIPTION_SYSTEM,
            user_prompt=_USER_PROMPTS[kind],
            image_bytes=image_bytes,
        )
    except OpenRouterError as exc:
        logger.info("Transcription (%s) failed: %s", kind.value, exc)
        raise TranscriptionError(
            _FAILURE_MESSAGES.get(type(exc), _DEFAULT_FAILURE)
        ) from exc

    # The model is told not to use code fences, but open-weight models do it
    # anyway; strip them defensively, same as the marking parser.
    text = strip_code_fences(completion.content).strip()
    if not text:
        raise TranscriptionError(_EMPTY_RESULT)

    return text
