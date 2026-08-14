"""Marking views.

Two audiences share one engine here.

``submit_paper`` is Sprint 2's JSON endpoint, kept because it is the quickest way
to exercise the engine without a browser. Everything else is Sprint 3's
teacher-facing portal. Neither contains any marking logic: both go through
``marking.submissions``, which is the only place that turns an upload into a
marked paper.

Every teacher view is scoped to the teacher who submitted the paper. There is no
class or roster structure in the MVP, so ownership of the submission is the only
boundary available, and it is a better default than letting any teacher read any
learner's marked work.
"""

import logging

from django.conf import settings
from django.contrib import messages
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from accounts.models import Role
from accounts.permissions import role_required
from core.images import ImageValidationError

from .forms import MemorandumForm, PaperUploadForm, TeacherMarkPaperForm
from .models import Memorandum, Paper
from .openrouter import (
    InsufficientCredit,
    ModelUnavailable,
    OpenRouterNotConfigured,
    RateLimited,
)
from .parsing import MarkingResponseError
from .submissions import remark, submit_for_marking
from .transcription import TranscriptionError, TranscriptionKind, transcribe_upload

logger = logging.getLogger(__name__)

# How each failure surfaces over HTTP on the JSON endpoint. 429 is passed through
# as itself so a caller can tell "wait and retry" apart from "this will never
# work".
STATUS_CODES = {
    RateLimited: 429,
    InsufficientCredit: 402,
    ModelUnavailable: 503,
    OpenRouterNotConfigured: 503,
    MarkingResponseError: 422,
}


# --------------------------------------------------------------------------- #
# Sprint 2: JSON endpoint
# --------------------------------------------------------------------------- #


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

    try:
        submission = submit_for_marking(
            uploaded_file=form.cleaned_data["image"],
            memorandum=form.cleaned_data["memorandum"],
            submitted_by=request.user,
        )
    except ImageValidationError as exc:
        return JsonResponse({"error": "invalid_image", "detail": str(exc)}, status=400)

    paper = submission.paper

    if not submission.succeeded:
        return JsonResponse(
            {
                "error": paper.failure_kind or "marking_failed",
                "detail": str(submission.error),
                "paper": _paper_payload(paper, submission.image),
                "retry_after": getattr(submission.error, "retry_after", None),
            },
            status=STATUS_CODES.get(type(submission.error), 502),
        )

    return JsonResponse(
        {
            "paper": _paper_payload(paper, submission.image),
            "result": _result_payload(submission.result),
        },
        status=201,
    )


def _paper_payload(paper, processed) -> dict:
    return {
        "id": paper.pk,
        "status": paper.status,
        "memorandum": paper.memorandum.title,
        "submitted_by": paper.submitted_by.username,
        "student": paper.student.username if paper.student_id else None,
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


# --------------------------------------------------------------------------- #
# Sprint 3: memorandums
# --------------------------------------------------------------------------- #


@role_required(Role.TEACHER)
def memorandum_list(request):
    """Every memorandum this teacher has written."""
    memorandums = (
        Memorandum.objects.filter(created_by=request.user)
        # So a teacher can see at a glance which guides are actually in use.
        .annotate(paper_count=Count("papers"), assignment_count=Count("assignments", distinct=True))
        .order_by("-created_at")
    )
    return render(
        request,
        "marking/memorandum_list.html",
        {"memorandums": memorandums, "nav_active": "memorandums"},
    )


@role_required(Role.TEACHER)
@require_http_methods(["GET", "POST"])
def memorandum_create(request):
    """Author a memorandum by typing it, or by photographing one.

    Two POST actions share this view. "transcribe" reads an uploaded photo into
    the content field and re-renders the form for review, saving nothing.
    "save" is the normal create. A teacher can also mix the two: transcribe,
    then edit what the model misread, then save.
    """
    if request.method == "POST" and request.POST.get("action") == "transcribe":
        return _transcribe_into_memorandum_form(request)

    form = MemorandumForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        memorandum = form.save(commit=False)
        memorandum.created_by = request.user
        memorandum.save()
        messages.success(
            request, f"Saved “{memorandum.title}”. You can now mark papers against it."
        )
        return redirect("marking:memorandum_list")

    return render(
        request,
        "marking/memorandum_form.html",
        {"form": form, "nav_active": "memorandums"},
    )


def _transcribe_into_memorandum_form(request):
    """Read a photographed memorandum into an unbound, pre-filled form.

    An unbound form with ``initial`` is used rather than a bound one so the
    teacher does not meet "this field is required" errors before they have tried
    to save. Whatever they had already typed is preserved; only the content is
    replaced by the transcription.
    """
    typed = {
        "title": request.POST.get("title", ""),
        "subject": request.POST.get("subject", ""),
        "total_marks": request.POST.get("total_marks", ""),
        "content": request.POST.get("content", ""),
    }

    def rendered(initial):
        return render(
            request,
            "marking/memorandum_form.html",
            {"form": MemorandumForm(initial=initial), "nav_active": "memorandums"},
        )

    image = request.FILES.get("image")
    if not image:
        messages.error(
            request, "Choose a photo first, or type the memorandum in below."
        )
        return rendered(typed)

    try:
        text = transcribe_upload(image, kind=TranscriptionKind.MEMORANDUM)
    except (ImageValidationError, TranscriptionError) as exc:
        messages.error(request, str(exc))
        return rendered(typed)

    typed["content"] = text
    messages.info(
        request,
        "We read this from your photo. Check it carefully and fix anything that "
        "is wrong before you save — it is your marking guide.",
    )
    return rendered(typed)


# --------------------------------------------------------------------------- #
# Sprint 3: marking a paper
# --------------------------------------------------------------------------- #


@role_required(Role.TEACHER)
@require_http_methods(["GET", "POST"])
def mark_paper(request):
    """Upload a learner's paper and mark it against a memorandum."""
    form = TeacherMarkPaperForm(
        request.POST or None, request.FILES or None, teacher=request.user
    )
    has_memorandums = Memorandum.objects.filter(created_by=request.user).exists()

    if request.method == "POST" and form.is_valid():
        try:
            submission = submit_for_marking(
                uploaded_file=form.cleaned_data["image"],
                memorandum=form.cleaned_data["memorandum"],
                submitted_by=request.user,
                student=form.cleaned_data["student"],
            )
        except ImageValidationError as exc:
            # Shown against the field the teacher needs to change.
            form.add_error("image", str(exc))
        else:
            if submission.succeeded:
                messages.success(
                    request,
                    f"Marked {submission.paper.belongs_to_display}'s paper.",
                )
            else:
                messages.error(request, submission.paper.failure_message)
            # Either way the paper exists and its page can explain itself.
            return redirect("marking:paper_detail", pk=submission.paper.pk)

    return render(
        request,
        "marking/mark_paper.html",
        {"form": form, "has_memorandums": has_memorandums, "nav_active": "mark"},
    )


@role_required(Role.TEACHER)
def paper_detail(request, pk):
    """One paper: the marks and per-question feedback, or why it failed."""
    paper = get_object_or_404(
        Paper.objects.select_related("memorandum", "student", "submitted_by", "result"),
        pk=pk,
        submitted_by=request.user,
    )

    questions = (
        paper.result.questions.all() if paper.status == Paper.Status.MARKED else []
    )

    return render(
        request,
        "marking/paper_detail.html",
        {"paper": paper, "questions": questions, "nav_active": "history"},
    )


@role_required(Role.TEACHER)
@require_POST
def paper_retry(request, pk):
    """Mark a stored paper again after a failure.

    The photo is already in storage, so a teacher does not have to find it
    again. The engine replaces any previous result for the paper.
    """
    paper = get_object_or_404(Paper, pk=pk, submitted_by=request.user)

    submission = remark(paper)

    if submission.succeeded:
        messages.success(request, "Marked. Here is the result.")
    else:
        messages.error(request, paper.failure_message)

    return redirect("marking:paper_detail", pk=paper.pk)


@role_required(Role.TEACHER)
def marking_history(request):
    """Everything this teacher has marked, most recent first."""
    papers = (
        Paper.objects.filter(submitted_by=request.user)
        .select_related("memorandum", "student", "result")
        .order_by("-created_at")
    )

    return render(
        request,
        "marking/marking_history.html",
        {
            "papers": papers,
            "marked_count": sum(1 for paper in papers if paper.status == Paper.Status.MARKED),
            "failed_count": sum(1 for paper in papers if paper.status == Paper.Status.FAILED),
            "nav_active": "history",
        },
    )
