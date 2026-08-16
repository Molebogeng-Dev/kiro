"""Teacher views for posting assignments and study material.

Both follow the same shape: a list of what this teacher has posted, and a form to
post another. The list matters as much as the form. Without it a teacher has no
confirmation that anything saved, and the natural response to that is to submit
again.

Student-facing views arrive in Sprint 4 and will read the same models.
"""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from accounts.models import Role
from accounts.permissions import role_required
from core.images import ImageValidationError
from marking.models import Memorandum, Paper
from marking.submissions import submit_for_marking
from marking.transcription import (
    TranscriptionError,
    TranscriptionKind,
    transcribe_upload,
)

from .forms import AssignmentForm, HomeworkSubmissionForm, StudyMaterialForm
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


# --------------------------------------------------------------------------- #
# Sprint 4: the student portal
# --------------------------------------------------------------------------- #
#
# Study materials and assignments are visible to every student for now, since
# there is no class or roster structure yet (the Sprint 3 limitation, carried
# forward). What is scoped per-student is the submission status and the results:
# a student sees whether *they* have submitted, and only their own marked work.


@role_required(Role.STUDENT)
def student_material_list(request):
    """Every study material a teacher has posted."""
    materials = StudyMaterial.objects.select_related("created_by").order_by(
        "-created_at"
    )
    return render(
        request,
        "classroom/student_material_list.html",
        {"materials": materials, "nav_active": "materials"},
    )


@role_required(Role.STUDENT)
def student_material_detail(request, pk):
    """One study material to read."""
    material = get_object_or_404(
        StudyMaterial.objects.select_related("created_by"), pk=pk
    )
    return render(
        request,
        "classroom/student_material_detail.html",
        {"material": material, "nav_active": "materials"},
    )


@role_required(Role.STUDENT)
def student_assignment_list(request):
    """Assignments, each flagged with whether this student has submitted.

    The submission status is computed from one query rather than one per
    assignment: fetch the student's latest paper per assignment up front and
    attach it, so a long list is still a couple of queries.
    """
    assignments = Assignment.objects.select_related("memorandum").order_by(
        "-created_at"
    )

    # assignment_id -> the student's most recent paper for it. Ordered newest
    # last so the dict ends up holding the newest per assignment.
    latest_by_assignment = {}
    student_papers = (
        Paper.objects.filter(student=request.user, assignment__isnull=False)
        .select_related("result")
        .order_by("created_at")
    )
    for paper in student_papers:
        latest_by_assignment[paper.assignment_id] = paper

    for assignment in assignments:
        assignment.student_paper = latest_by_assignment.get(assignment.id)

    return render(
        request,
        "classroom/student_assignment_list.html",
        {"assignments": assignments, "nav_active": "assignments"},
    )


@role_required(Role.STUDENT)
@require_http_methods(["GET", "POST"])
def submit_homework(request, pk):
    """Submit a photo of homework for one assignment.

    The assignment comes from the URL and the learner from ``request.user`` —
    never from the request body — so a submission cannot be attributed to
    another student. This goes through the same ``submit_for_marking`` path as
    teacher marking; nothing about the pipeline is re-implemented here.
    """
    assignment = get_object_or_404(
        Assignment.objects.select_related("memorandum"), pk=pk
    )
    form = HomeworkSubmissionForm(request.POST or None, request.FILES or None)

    if request.method == "POST" and form.is_valid():
        try:
            submission = submit_for_marking(
                uploaded_file=form.cleaned_data["image"],
                memorandum=assignment.memorandum,
                submitted_by=request.user,
                # Server-set from the session. The security requirement of this
                # sprint: a student can never submit work as someone else.
                student=request.user,
                assignment=assignment,
            )
        except ImageValidationError as exc:
            form.add_error("image", str(exc))
        else:
            if submission.succeeded:
                messages.success(
                    request, "Homework submitted and marked. Here is your result."
                )
            else:
                messages.error(request, submission.paper.failure_message)
            return redirect("marking:my_result_detail", pk=submission.paper.pk)

    return render(
        request,
        "classroom/submit_homework.html",
        {"assignment": assignment, "form": form, "nav_active": "assignments"},
    )
