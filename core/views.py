"""Role dashboards.

Placeholders for Sprint 1: each one proves the routing and the access control
work. Real content arrives in later sprints.
"""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render

from accounts.models import Role
from accounts.permissions import role_required


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
    return render(request, "core/dashboard_teacher.html")


@role_required(Role.STUDENT)
def student_dashboard(request):
    return render(request, "core/dashboard_student.html")


@role_required(Role.PARENT)
def parent_dashboard(request):
    return render(request, "core/dashboard_parent.html")
