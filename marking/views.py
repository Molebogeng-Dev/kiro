"""The marking test endpoint.

One view, deliberately. It exists to prove the engine works end to end and to be
the thing a demo drives; it is not the teacher or student experience, which
arrive in Sprints 3 and 4 and will call ``engine.mark_paper`` themselves.

It requires a logged-in teacher or student. An open upload endpoint that calls a
paid AI provider is a bill waiting to happen, quite apart from letting anyone
push images into our storage bucket.
"""

import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from accounts.models import Role
from accounts.permissions import role_required
from core.images import ImageValidationError, process_upload
from django.conf import settings

from .engine import mark_paper
from .forms import PaperUploadForm
from .models import Paper
from .openrouter import (
    InsufficientCredit,
    ModelUnavailable,
    OpenRouterError,
    OpenRouterNotConfigured,
    RateLimited,
)
from .parsing import MarkingResponseError

logger = logging.getLogger(__name__)

# How each failure surfaces over HTTP. 429 is passed through as itself so a
# caller can tell "wait and retry" apart from "this will never work".
STATUS_CODES = {
    RateLimited: 429,
    InsufficientCredit: 402,
    ModelUnavailable: 503,
    OpenRouterNotConfigured: 503,
    MarkingResponseError: 422,
}


@role_required(Role.TEACHER, Role.STUDENT)
@require_http_methods(["GET", "POST"])
def submit_paper(request):
    """GET renders a minimal upload form; POST marks the paper and returns JSON."""
    if request.method == "GET":
        return render(
            request,
            "marking/submit_paper.html",
            {"form": PaperUploadForm(), "model": settings.OPENROUTER_MODEL},
        )

    form = PaperUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        return JsonResponse(
            {"error": "invalid_request", "detail": form.errors}, status=400
        )

    memorandum = form.cleaned_data["memorandum"]

    try:
        processed = process_upload(
            form.cleaned_data["image"],
            max_dimension=settings.MARKING_IMAGE_MAX_DIMENSION,
            jpeg_quality=settings.MARKING_IMAGE_JPEG_QUALITY,
            max_bytes=settings.MARKING_MAX_UPLOAD_BYTES,
        )
    except ImageValidationError as exc:
        return JsonResponse({"error": "invalid_image", "detail": str(exc)}, status=400)

    paper = _create_paper(request.user, memorandum, processed)

    try:
        result = mark_paper(paper, image_bytes=processed.data)
    except (OpenRouterError, MarkingResponseError) as exc:
        # mark_paper has already recorded the failure against the paper, so the
        # submission is preserved and can be retried later.
        return JsonResponse(
            {
                "error": paper.failure_kind or "marking_failed",
                "detail": str(exc),
                "paper": _paper_payload(paper, processed),
                "retry_after": getattr(exc, "retry_after", None),
            },
            status=STATUS_CODES.get(type(exc), 502),
        )

    return JsonResponse(
        {
            "paper": _paper_payload(paper, processed),
            "result": _result_payload(result),
        },
        status=201,
    )


def _create_paper(user, memorandum, processed) -> Paper:
    with transaction.atomic():
        paper = Paper(submitted_by=user, memorandum=memorandum)
        paper.full_clean(exclude=["image"])
        # Saving the image is what uploads it to Supabase Storage. The filename
        # is ignored: paper_image_path assigns a UUID path.
        paper.image.save("paper.jpg", processed.as_content_file(), save=False)
        paper.save()
    return paper


def _paper_payload(paper, processed) -> dict:
    return {
        "id": paper.pk,
        "status": paper.status,
        "memorandum": paper.memorandum.title,
        "submitted_by": paper.submitted_by.username,
        "created_at": paper.created_at.isoformat(),
        "stored_path": paper.image.name,
        "image": processed.as_dict(),
    }


def _result_payload(result) -> dict:
    return {
        "marks_awarded": float(result.marks_awarded),
        "marks_available": float(result.marks_available),
        "percentage": result.percentage,
        "summary": result.summary,
        "model_used": result.model_used,
        "questions": [
            {
                "number": question.number,
                "marks_awarded": float(question.marks_awarded),
                "marks_available": float(question.marks_available),
                "feedback": question.feedback,
            }
            for question in result.questions.all()
        ],
    }
