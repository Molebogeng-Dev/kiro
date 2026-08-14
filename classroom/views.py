"""Teacher views for posting assignments and study material.

Both follow the same shape: a list of what this teacher has posted, and a form to
post another. The list matters as much as the form. Without it a teacher has no
confirmation that anything saved, and the natural response to that is to submit
again.

Student-facing views arrive in Sprint 4 and will read the same models.
"""

from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from accounts.models import Role
from accounts.permissions import role_required
from core.images import ImageValidationError
from marking.models import Memorandum
from marking.transcription import (
    TranscriptionError,
    TranscriptionKind,
    transcribe_upload,
)

from .forms import AssignmentForm, StudyMaterialForm
from .models import Assignment, StudyMaterial


@role_required(Role.TEACHER)
def assignment_list(request):
    assignments = (
        Assignment.objects.filter(created_by=request.user)
        .select_related("memorandum")
        .order_by("-created_at")
    )
    return render(
        request,
        "classroom/assignment_list.html",
        {"assignments": assignments, "nav_active": "assignments"},
    )


@role_required(Role.TEACHER)
@require_http_methods(["GET", "POST"])
def assignment_create(request):
    """Post an assignment by typing it, or by photographing the instructions.

    Mirrors memorandum authoring: a "transcribe" action reads a photo into the
    instructions for the teacher to check, and "save" is the normal create.
    """
    if request.method == "POST" and request.POST.get("action") == "transcribe":
        return _transcribe_into_assignment_form(request)

    form = AssignmentForm(request.POST or None, teacher=request.user)
    # An assignment needs a memorandum, so say so before the teacher fills
    # everything in and hits a validation error on an empty dropdown.
    has_memorandums = Memorandum.objects.filter(created_by=request.user).exists()

    if request.method == "POST" and form.is_valid():
        assignment = form.save(commit=False)
        assignment.created_by = request.user
        assignment.save()
        messages.success(
            request,
            f"Posted “{assignment.title}”. Learners will see it in their portal.",
        )
        return redirect("classroom:assignment_list")

    return render(
        request,
        "classroom/assignment_form.html",
        {"form": form, "has_memorandums": has_memorandums, "nav_active": "assignments"},
    )


def _transcribe_into_assignment_form(request):
    """Read photographed instructions into an unbound, pre-filled form.

    As with memorandums, an unbound form with ``initial`` avoids premature
    validation errors and preserves the title, memorandum, and due date the
    teacher may have already chosen; only the instructions are replaced.
    """
    typed = {
        "title": request.POST.get("title", ""),
        "memorandum": request.POST.get("memorandum", ""),
        "instructions": request.POST.get("instructions", ""),
        "due_date": request.POST.get("due_date", ""),
    }

    def rendered(initial):
        return render(
            request,
            "classroom/assignment_form.html",
            {
                "form": AssignmentForm(teacher=request.user, initial=initial),
                "has_memorandums": Memorandum.objects.filter(
                    created_by=request.user
                ).exists(),
                "nav_active": "assignments",
            },
        )

    image = request.FILES.get("image")
    if not image:
        messages.error(
            request, "Choose a photo first, or type the instructions in below."
        )
        return rendered(typed)

    try:
        text = transcribe_upload(image, kind=TranscriptionKind.ASSIGNMENT)
    except (ImageValidationError, TranscriptionError) as exc:
        messages.error(request, str(exc))
        return rendered(typed)

    typed["instructions"] = text
    messages.info(
        request,
        "We read this from your photo. Check the instructions and fix anything "
        "that is wrong before you post it.",
    )
    return rendered(typed)


@role_required(Role.TEACHER)
def material_list(request):
    materials = StudyMaterial.objects.filter(created_by=request.user).order_by(
        "-created_at"
    )
    return render(
        request,
        "classroom/material_list.html",
        {"materials": materials, "nav_active": "materials"},
    )


@role_required(Role.TEACHER)
@require_http_methods(["GET", "POST"])
def material_create(request):
    form = StudyMaterialForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        material = form.save(commit=False)
        material.created_by = request.user
        material.save()
        messages.success(
            request,
            f"Posted “{material.title}”. Learners will see it in their portal.",
        )
        return redirect("classroom:material_list")

    return render(
        request,
        "classroom/material_form.html",
        {"form": form, "nav_active": "materials"},
    )
