"""One path from an uploaded file to a marked paper.

Sprint 2's JSON endpoint and Sprint 3's teacher page both need to validate an
image, compress it, store it, and hand it to the engine. That sequence lives here
so there is exactly one of it: two copies would drift, and the copy that drifted
would be the one that silently stopped compressing images or stopped recording
whose work it was.

Marking failures are returned rather than raised. Both callers need to keep the
paper and show something useful about it, and the difference between them is only
how they present it: JSON status codes in one case, a page with a retry button in
the other.
"""

import logging
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction

from accounts.models import Role
from core.images import ProcessedImage, process_upload

from .engine import mark_paper
from .models import MarkingResult, Paper
from .openrouter import OpenRouterError
from .parsing import MarkingResponseError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Submission:
    """The outcome of one upload.

    ``paper`` is always present, even when marking failed: losing a learner's
    submission because a provider was busy is the one thing this must never do.
    """

    paper: Paper
    image: ProcessedImage
    result: MarkingResult | None = None
    error: Exception | None = None

    @property
    def succeeded(self) -> bool:
        return self.result is not None


def process_paper_image(uploaded_file) -> ProcessedImage:
    """Validate and compress an upload. Raises ``ImageValidationError``."""
    return process_upload(
        uploaded_file,
        max_dimension=settings.MARKING_IMAGE_MAX_DIMENSION,
        jpeg_quality=settings.MARKING_IMAGE_JPEG_QUALITY,
        max_bytes=settings.MARKING_MAX_UPLOAD_BYTES,
    )


def create_paper(
    *, submitted_by, memorandum, processed, student=None, assignment=None
) -> Paper:
    """Store the image and record the paper.

    When a learner uploads their own homework, they are both the submitter and
    the subject, so the learner is filled in automatically. A teacher has to say
    whose work it is. ``assignment`` ties a homework submission back to the work
    it answers, and is left unset for a loose paper a teacher is marking.
    """
    if student is None and submitted_by.role == Role.STUDENT:
        student = submitted_by

    with transaction.atomic():
        paper = Paper(
            submitted_by=submitted_by,
            memorandum=memorandum,
            student=student,
            assignment=assignment,
        )
        paper.full_clean(exclude=["image"])
        # Saving the image is what uploads it to Supabase Storage. The filename
        # is ignored: paper_image_path assigns a UUID path.
        paper.image.save("paper.jpg", processed.as_content_file(), save=False)
        paper.save()

    return paper


def submit_for_marking(
    *, uploaded_file, memorandum, submitted_by, student=None, assignment=None
) -> Submission:
    """Validate, store, and mark in one call.

    Raises ``ImageValidationError`` if the upload is not a usable image, since
    there is nothing to keep in that case. Any failure after the paper exists is
    returned on the ``Submission`` instead.
    """
    processed = process_paper_image(uploaded_file)
    paper = create_paper(
        submitted_by=submitted_by,
        memorandum=memorandum,
        processed=processed,
        student=student,
        assignment=assignment,
    )

    try:
        result = mark_paper(paper, image_bytes=processed.data)
    except (OpenRouterError, MarkingResponseError) as exc:
        # mark_paper has already recorded the failure kind against the paper.
        logger.info("Paper %s could not be marked: %s", paper.pk, exc)
        return Submission(paper=paper, image=processed, error=exc)

    return Submission(paper=paper, image=processed, result=result)


def remark(paper) -> Submission:
    """Try again on a paper already in storage.

    Used by the retry button on a failed paper. The image is read back from
    storage rather than re-uploaded, so a teacher does not have to find the
    photo again, and the engine replaces any previous result for the paper.
    """
    try:
        result = mark_paper(paper)
    except (OpenRouterError, MarkingResponseError) as exc:
        return Submission(paper=paper, image=None, error=exc)

    return Submission(paper=paper, image=None, result=result)
