"""Role dashboards.

The teacher dashboard became a real hub in Sprint 3: the actions a teacher needs,
and a short list of what they marked recently so the page proves the app is
working rather than just linking elsewhere. The student and parent dashboards are
still placeholders, filled in by Sprints 4 and 6.
"""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render

from accounts.models import Role
from accounts.permissions import role_required
from classroom.models import Assignment, StudyMaterial
from marking.models import Memorandum, Paper

# Enough to show the work is happening, few enough that the actions stay visible
# without scrolling on a laptop.
RECENT_PAPER_COUNT = 5


@login_required
def home(request):
    """Send a logged-in user to the dashboard for their role."""
    url_name = request.user.dashboard_url_name
    if url_name is None:
        raise PermissionDenied(
            "This account has no role assigned. An administrator needs to set "
            "one before you can use iSgela."
        )
    return redirect(url_name)


@role_required(Role.TEACHER)
def teacher_dashboard(request):
    recent_papers = (
        Paper.objects.filter(submitted_by=request.user)
        .select_related("memorandum", "student", "result")
        .order_by("-created_at")[:RECENT_PAPER_COUNT]
    )

    return render(
        request,
        "core/dashboard_teacher.html",
        {
            "recent_papers": recent_papers,
            # Drives which prompt the empty state shows: write a guide first, or
            # go ahead and mark. Scoped to this teacher, matching the rest of
            # the portal.
            "has_memorandums": Memorandum.objects.filter(
                created_by=request.user
            ).exists(),
            "first_name": request.user.first_name,
            "nav_active": "dashboard",
        },
    )


@role_required(Role.STUDENT)
def student_dashboard(request):
    recent_papers = (
        Paper.objects.filter(student=request.user)
        .select_related("memorandum", "assignment", "result")
        .order_by("-created_at")[:RECENT_PAPER_COUNT]
    )

    # Assignments this student has not submitted yet, so the dashboard can nudge
    # toward the next thing to do rather than just linking around.
    submitted_assignment_ids = Paper.objects.filter(
        student=request.user, assignment__isnull=False
    ).values_list("assignment_id", flat=True)
    to_do_count = Assignment.objects.exclude(id__in=submitted_assignment_ids).count()

    return render(
        request,
        "core/dashboard_student.html",
        {
            "recent_papers": recent_papers,
            "to_do_count": to_do_count,
            "has_materials": StudyMaterial.objects.exists(),
            "first_name": request.user.first_name,
            "nav_active": "dashboard",
        },
    )


@role_required(Role.PARENT)
def parent_dashboard(request):
    return render(request, "core/dashboard_parent.html", {"nav_active": "dashboard"})
