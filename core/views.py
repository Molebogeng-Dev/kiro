"""Role dashboards.

The teacher dashboard became a real hub in Sprint 3: the actions a teacher needs,
and a short list of what they marked recently so the page proves the app is
working rather than just linking elsewhere. The student and parent dashboards are
still placeholders, filled in by Sprints 4 and 6.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from accounts.forms import TeacherInviteForm
from accounts.models import Role
from accounts.permissions import role_required
from accounts.scoping import scope_by_school, students_at_school
from attendance.models import Attendance
from classroom.models import Assignment, StudyMaterial
from marking.models import Memorandum, Paper

from .progress import build_class_overview, build_student_rollup

# Enough to show the work is happening, few enough that the actions stay visible
# without scrolling on a laptop.
RECENT_PAPER_COUNT = 5

# Attendance rows shown on a child's parent page.
RECENT_ATTENDANCE_DAYS = 10


def home(request):
    """The app's front door.

    An anonymous visitor gets the public landing page that introduces iSgela.
    A signed-in user is sent straight to their role's dashboard, so the app
    still opens on their own workspace the moment they have an account — the
    behaviour this view had before the landing page existed.
    """
    if not request.user.is_authenticated:
        return render(request, "core/landing.html", {"nav_active": "home"})

    url_name = request.user.dashboard_url_name
    if url_name is None:
        raise PermissionDenied(
            "This account has no role assigned. An administrator needs to set "
            "one before you can use iSgela."
        )
    return redirect(url_name)


def about(request):
    """The public 'About iSgela' page.

    Holds the longer story the landing page used to carry inline — the problem
    iSgela solves, what it does, and a way in. It's reachable by anyone, signed
    in or not, and is linked from the About frame on the landing page.
    """
    return render(request, "core/about.html", {"nav_active": "about"})


@role_required(Role.SCHOOL_ADMIN)
def school_admin_dashboard(request):
    """The school admin's hub: the join codes to share, and the teacher list.

    Listing a teacher creates a single-use ``TeacherInvite`` — a slot, not an
    account — that the teacher claims when they register. The school is always
    the admin's own (``request.user.school``), never taken from the request.
    """
    school = request.user.school
    if school is None:
        # A school_admin created through registration always has a school; this
        # is only reachable for an oddly-provisioned account, so say so plainly
        # rather than 500.
        return render(
            request,
            "core/dashboard_school_admin.html",
            {"school": None, "nav_active": "dashboard"},
        )

    if request.method == "POST":
        form = TeacherInviteForm(request.POST, school=school)
        if form.is_valid():
            invite = form.save()
            messages.success(
                request,
                f"Listed {invite.teacher_name}. Their single-use code is "
                f"{invite.code} \u2014 share it with them to register.",
            )
            return redirect("core:school_admin_dashboard")
    else:
        form = TeacherInviteForm(school=school)

    invites = school.teacher_invites.select_related("claimed_by").all()

    return render(
        request,
        "core/dashboard_school_admin.html",
        {
            "school": school,
            "form": form,
            "invites": invites,
            "open_invite_count": sum(1 for invite in invites if not invite.is_claimed),
            "nav_active": "dashboard",
        },
    )


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
    # toward the next thing to do rather than just linking around. Scoped to the
    # student's school (Sprint 8b), to match the now school-scoped lists — the
    # dashboard count must not include another school's assignments.
    submitted_assignment_ids = Paper.objects.filter(
        student=request.user, assignment__isnull=False
    ).values_list("assignment_id", flat=True)
    school_assignments = scope_by_school(
        Assignment.objects.all(), request.user.school, field="created_by__school"
    )
    to_do_count = school_assignments.exclude(id__in=submitted_assignment_ids).count()

    return render(
        request,
        "core/dashboard_student.html",
        {
            "recent_papers": recent_papers,
            "to_do_count": to_do_count,
            "has_materials": scope_by_school(
                StudyMaterial.objects.all(),
                request.user.school,
                field="created_by__school",
            ).exists(),
            "first_name": request.user.first_name,
            "nav_active": "dashboard",
        },
    )


# --------------------------------------------------------------------------- #
# Sprint 7: the teacher progress dashboard
# --------------------------------------------------------------------------- #
#
# Access follows attendance (Sprint 5), not marking ownership (Sprint 3): any
# teacher may view any learner's rollup. Without class or roster structure, a
# whole-school progress view is more useful than one scoped to "papers I
# personally marked" — the same reasoning attendance used. It is read-only; it
# aggregates data other sprints already produced and collects nothing new.


@role_required(Role.TEACHER)
def progress_dashboard(request):
    """Every learner at this teacher's school, each with a needs-attention flag."""
    rollups = build_class_overview(request.user.school)
    flagged = [rollup for rollup in rollups if rollup.needs_attention]
    on_track = [rollup for rollup in rollups if not rollup.needs_attention]

    return render(
        request,
        "core/progress_list.html",
        {
            # Flagged learners first so a teacher sees who needs help without
            # scrolling; alphabetical within each group (from the query).
            "students": flagged + on_track,
            "flagged_count": len(flagged),
            "total_count": len(rollups),
            "nav_active": "progress",
        },
    )


@role_required(Role.TEACHER)
def progress_student(request, student_id):
    """One learner's rollup: per-subject marks, attendance, assignments, and the
    reasons behind any flag — so a teacher sees *why*, not just *that*.

    Scoped to the acting teacher's school (Sprint 8b): a learner at another
    school is filtered out before the lookup and 404s, the same ownership-
    boundary pattern the student and parent portals use.
    """
    student = get_object_or_404(
        students_at_school(request.user.school), id=student_id
    )
    rollup = build_student_rollup(student)

    return render(
        request,
        "core/progress_student.html",
        {"student": student, "rollup": rollup, "nav_active": "progress"},
    )


@role_required(Role.PARENT)
def parent_dashboard(request):
    """List the parent's linked children.

    With exactly one child, skip the picker and go straight to their page —
    most parents have one learner and should not have to click through a list
    of one. With several, or none, render the dashboard.
    """
    children = list(request.user.children)
    if len(children) == 1:
        return redirect("core:parent_child", child_id=children[0].id)

    return render(
        request,
        "core/dashboard_parent.html",
        {"children": children, "nav_active": "dashboard"},
    )


@role_required(Role.PARENT)
def parent_child(request, child_id):
    """One linked child's recent marked papers and attendance.

    Access is via the parent/student link: ``request.user.children`` is the
    queryset of linked learners, so a child_id that is not linked is excluded
    before the lookup and returns 404 — the same ownership boundary as the
    student portal, never a "you may not" page that confirms the child exists.
    """
    child = get_object_or_404(request.user.children, id=child_id)

    recent_papers = (
        Paper.objects.filter(student=child, status=Paper.Status.MARKED)
        .select_related("memorandum", "assignment", "result")
        .order_by("-created_at")[:RECENT_PAPER_COUNT]
    )
    recent_attendance = Attendance.objects.filter(student=child).order_by("-date")[
        :RECENT_ATTENDANCE_DAYS
    ]

    return render(
        request,
        "core/parent_child.html",
        {
            "child": child,
            # For a switcher when a parent has more than one child.
            "children": request.user.children,
            "recent_papers": recent_papers,
            "recent_attendance": recent_attendance,
            "nav_active": "dashboard",
        },
    )


@role_required(Role.PARENT)
def parent_paper(request, pk):
    """A marked paper of one of the parent's children, read-only.

    Reuses the same ``marking/_result_body.html`` the teacher and student see.
    Restricted to ``status="marked"`` papers of linked children, filtered before
    the lookup, so another family's paper (or an unmarked one) is a 404.
    """
    paper = get_object_or_404(
        Paper.objects.select_related("memorandum", "assignment", "student", "result"),
        pk=pk,
        status=Paper.Status.MARKED,
        student__in=request.user.children,
    )

    return render(
        request,
        "core/parent_paper.html",
        {
            "paper": paper,
            "questions": paper.result.questions.all(),
            "nav_active": "dashboard",
        },
    )
