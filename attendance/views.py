"""Teacher-facing attendance views.

Unlike Sprint 3's marking pages, none of these are scoped to the teacher who
created a record. Attendance is a shared front-desk function: any teacher can
run the roll-call, enroll a face, work the check-in station, or read today's
list. So the boundary here is only "is a teacher", never "is *this* teacher".

Grade routing is enforced in both directions on the server, not by hiding
buttons: a primary learner can never be enrolled for facial check-in, and a
secondary learner never appears in the manual roll-call list (they reach manual
marking only through the fallback).
"""

import logging

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from accounts.models import Role
from accounts.permissions import role_required

from .forms import (
    FaceEnrollmentForm,
    FallbackMarkForm,
    primary_students,
    secondary_students,
)
from .matching import InvalidDescriptor, best_match, parse_descriptor
from .models import Attendance, FaceEnrollment
from .services import mark_present_manually, record_scan

logger = logging.getLogger(__name__)


def _faceapi_context():
    """The two URLs the browser needs to load face-api.js and its weights."""
    return {
        "faceapi_script_url": settings.FACEAPI_SCRIPT_URL,
        "faceapi_model_url": settings.FACEAPI_MODEL_URL,
    }


def _present_student_ids_today():
    return set(
        Attendance.objects.filter(
            date=timezone.localdate(), arrived_at__isnull=False
        ).values_list("student_id", flat=True)
    )


@role_required(Role.TEACHER)
def attendance_index(request):
    """A small hub linking to each attendance task, with today's tallies."""
    present_ids = _present_student_ids_today()
    return render(
        request,
        "attendance/index.html",
        {
            "present_today": len(present_ids),
            "primary_total": primary_students().count(),
            "secondary_total": secondary_students().count(),
            "enrolled_total": FaceEnrollment.objects.count(),
            "nav_active": "attendance",
        },
    )


@role_required(Role.TEACHER)
@require_http_methods(["GET", "POST"])
def roll_call(request):
    """Manual register for primary students (grades 1-7)."""
    students = list(primary_students())

    if request.method == "POST":
        # Only ids that are genuinely primary students are honoured; anything
        # else in the POST (a tampered secondary id) is filtered out here, so
        # routing is enforced server-side, not just by the rendered list.
        allowed_ids = {str(student.id) for student in students}
        chosen = [
            student
            for student in students
            if str(student.id) in set(request.POST.getlist("present"))
            and str(student.id) in allowed_ids
        ]
        for student in chosen:
            mark_present_manually(student)

        messages.success(
            request,
            f"Marked {len(chosen)} student{'' if len(chosen) == 1 else 's'} present.",
        )
        return redirect("attendance:roll_call")

    present_ids = _present_student_ids_today()
    for student in students:
        student.is_present_today = student.id in present_ids

    return render(
        request,
        "attendance/roll_call.html",
        {
            "students": students,
            "present_count": sum(1 for s in students if s.is_present_today),
            "today": timezone.localdate(),
            "nav_active": "attendance",
        },
    )


@role_required(Role.TEACHER)
@require_http_methods(["GET", "POST"])
def enroll(request):
    """Enroll a secondary student's face, with explicit consent."""
    form = FaceEnrollmentForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        student = form.cleaned_data["student"]
        # update_or_create, so re-enrolling refreshes the descriptor rather
        # than leaving a stale one or duplicating the row.
        FaceEnrollment.objects.update_or_create(
            student=student,
            defaults={
                "descriptor": form.cleaned_data["descriptor"],
                "enrolled_by": request.user,
                "consent_confirmed": True,
            },
        )
        messages.success(
            request,
            f"Enrolled {student.get_full_name() or student.username} for facial "
            "check-in.",
        )
        return redirect("attendance:enroll")

    return render(
        request,
        "attendance/enroll.html",
        {
            "form": form,
            "enrolled": FaceEnrollment.objects.select_related("student").all(),
            **_faceapi_context(),
            "nav_active": "attendance",
        },
    )


@role_required(Role.TEACHER)
@require_http_methods(["GET", "POST"])
def check_in(request):
    """Per-period facial check-in for secondary students, pass-the-device style.

    The teacher points the camera at each student in turn; the browser posts a
    descriptor computed from the frame, and the match is decided here. Every
    match records a scan that rolls up into the day's attendance. A no-match is
    not a dead end: the same manual fallback below records a scan by hand.
    """
    result = None
    matched = None
    first_today = False

    if request.method == "POST":
        try:
            descriptor = parse_descriptor(_loads(request.POST.get("descriptor", "")))
        except InvalidDescriptor as exc:
            messages.error(request, f"That capture could not be read: {exc}")
        else:
            match = best_match(
                descriptor,
                FaceEnrollment.objects.select_related("student"),
                threshold=settings.ATTENDANCE_FACE_MATCH_THRESHOLD,
            )
            if match is None:
                result = "no_match"
                messages.error(
                    request,
                    "No enrolled student was recognised. Mark them present by "
                    "hand below if you know who it is.",
                )
            else:
                matched = match.enrollment.student
                _, _, first_today = record_scan(
                    matched,
                    method=Attendance.Method.FACIAL,
                    recorded_by=request.user,
                )
                result = "scan"
                messages.success(
                    request,
                    f"{matched.get_full_name() or matched.username} checked in "
                    f"for this period.",
                )

    return render(
        request,
        "attendance/check_in.html",
        {
            "result": result,
            "matched": matched,
            "first_today": first_today,
            "fallback_form": FallbackMarkForm(),
            **_faceapi_context(),
            "nav_active": "attendance",
        },
    )


@role_required(Role.TEACHER)
@require_POST
def mark_present(request):
    """The manual fallback when a secondary student's face will not match.

    It records a manual-method scan through the same ``record_scan`` a facial
    check-in uses — a period check-in is a period check-in, so there is no
    separate manual path and no arrival/departure to decide. The form's queryset
    is secondary-only, keeping primary learners in the roll-call where they
    belong.
    """
    form = FallbackMarkForm(request.POST)
    if form.is_valid():
        student = form.cleaned_data["student"]
        record_scan(student, method=Attendance.Method.MANUAL, recorded_by=request.user)
        messages.success(
            request,
            f"Marked {student.get_full_name() or student.username} present.",
        )
    else:
        messages.error(request, "Choose a student to mark present.")

    return redirect("attendance:check_in")


@role_required(Role.TEACHER)
def history(request):
    """Everyone marked present today, across both methods. Any teacher."""
    records = (
        Attendance.objects.filter(date=timezone.localdate())
        .select_related("student")
        .order_by("student__grade", "student__username")
    )
    return render(
        request,
        "attendance/history.html",
        {
            "records": records,
            "today": timezone.localdate(),
            "nav_active": "attendance",
        },
    )


def _loads(raw):
    """Tolerant JSON load: accept a JSON string, or a value already parsed."""
    import json

    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None
