"""Read-only progress aggregation for the teacher dashboard (Sprint 7).

This module answers one question three ways: how is a learner doing? It rolls up
data three other sprints already produce — marked papers (Sprints 2-4),
attendance (Sprint 5), and assignment submissions (Sprint 4) — into a per-subject
mark average, an attendance rate, and an assignment-completion count, and it
decides, by plain rules, whether a learner needs a teacher's attention.

Two deliberate design choices:

* **The "needs attention" flag is rule-based, never an AI call.** A teacher who
  asks "why is this learner flagged?" gets a specific, checkable answer — an
  average below a threshold, attendance below a threshold, missed assignments —
  not a black box. The thresholds are constants right here, so tuning one is a
  one-line change, and the reasons are returned as text the dashboard shows.

* **A "school day" is a day attendance was actually taken.** There is no school
  calendar in the MVP, and absence is implicit (a learner with no row for a day
  was not marked present). So the attendance window is the most recent N
  *distinct dates on which any attendance was recorded*, and a learner's rate is
  how many of those days they were present. This is self-calibrating and
  explainable, and it avoids punishing a learner for weekends and holidays that
  were never school days.

Nothing here writes anything. It is aggregation over existing rows.
"""

from dataclasses import dataclass

from django.db.models import Count

from accounts.models import Role, User
from attendance.models import Attendance
from classroom.models import Assignment
from marking.models import Paper

# --------------------------------------------------------------------------- #
# Thresholds — the whole definition of "needs attention", in one readable place
# --------------------------------------------------------------------------- #

# A learner whose average mark is below this (percent) is flagged.
LOW_MARK_THRESHOLD = 50.0

# A learner present on fewer than this share (percent) of recent school days is
# flagged.
LOW_ATTENDANCE_THRESHOLD = 80.0

# How many recent school days the attendance rate is measured over.
ATTENDANCE_WINDOW_DAYS = 10

# This many assignments past their due date with nothing submitted flags a
# learner. One slip is not a pattern; two starts to be.
MISSED_ASSIGNMENT_THRESHOLD = 2


# --------------------------------------------------------------------------- #
# Result shapes
# --------------------------------------------------------------------------- #


@dataclass
class SubjectAverage:
    subject: str
    average: float  # mean of this subject's paper percentages
    paper_count: int


@dataclass
class AttendanceSummary:
    school_days: int  # size of the window actually available
    present_days: int
    rate: float | None  # percent, or None when no attendance has been taken yet


@dataclass
class AssignmentCompletion:
    total: int
    submitted: int  # distinct assignments this learner has submitted for
    missed_past_due: int  # past their due date, nothing submitted

    @property
    def outstanding(self) -> int:
        return max(self.total - self.submitted, 0)


@dataclass
class StudentRollup:
    student: User
    subjects: list[SubjectAverage]
    overall_average: float | None
    attendance: AttendanceSummary
    assignments: AssignmentCompletion
    reasons: list[str]

    @property
    def needs_attention(self) -> bool:
        return bool(self.reasons)


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #


def build_student_rollup(student, *, window_days=ATTENDANCE_WINDOW_DAYS) -> StudentRollup:
    """The full picture for one learner — the per-student detail view."""
    school_days = recent_school_days(window_days)
    assignments = list(Assignment.objects.all())

    papers = _marked_papers([student.id])
    submitted_pairs = _submitted_pairs([student.id])
    present_counts = _present_counts([student.id], school_days)
    has_history = Attendance.objects.filter(student=student).exists()

    return _assemble(
        student,
        papers.get(student.id, []),
        school_days,
        present_counts.get(student.id, 0),
        has_history,
        assignments,
        {aid for (sid, aid) in submitted_pairs if sid == student.id},
    )


def build_class_overview(*, window_days=ATTENDANCE_WINDOW_DAYS) -> list[StudentRollup]:
    """A rollup per learner, for the scannable student list.

    Built from a handful of bulk queries rather than one set per learner, so a
    whole school's list stays a few queries, not a few hundred. Every learner
    gets the same rollup the detail view would build, so the flag on the list
    and the reasons on the detail page can never disagree.
    """
    students = list(
        User.objects.filter(role=Role.STUDENT).order_by(
            "first_name", "last_name", "username"
        )
    )
    if not students:
        return []

    student_ids = [student.id for student in students]
    school_days = recent_school_days(window_days)
    assignments = list(Assignment.objects.all())

    papers = _marked_papers(student_ids)
    submitted_pairs = _submitted_pairs(student_ids)
    present_counts = _present_counts(student_ids, school_days)
    history_ids = _attendance_history_ids(student_ids)

    overview = []
    for student in students:
        submitted_ids = {aid for (sid, aid) in submitted_pairs if sid == student.id}
        overview.append(
            _assemble(
                student,
                papers.get(student.id, []),
                school_days,
                present_counts.get(student.id, 0),
                student.id in history_ids,
                assignments,
                submitted_ids,
            )
        )
    return overview


def recent_school_days(window_days=ATTENDANCE_WINDOW_DAYS) -> list:
    """The most recent distinct dates on which any attendance was recorded.

    These are the "school days" the attendance rate is measured against. An
    explicit ``order_by`` keeps the model's default two-field ordering from
    breaking the DISTINCT.
    """
    return list(
        Attendance.objects.order_by("-date")
        .values_list("date", flat=True)
        .distinct()[:window_days]
    )


# --------------------------------------------------------------------------- #
# Assembly and the flag rules
# --------------------------------------------------------------------------- #


def _assemble(
    student, papers, school_days, present_days, has_attendance_history,
    assignments, submitted_ids,
):
    subjects = _subject_averages(papers)
    overall = _overall_average(papers)
    attendance = _attendance_summary(
        len(school_days), present_days, has_attendance_history
    )
    completion = _assignment_completion(assignments, submitted_ids)
    reasons = evaluate_reasons(overall, attendance, completion)
    return StudentRollup(
        student=student,
        subjects=subjects,
        overall_average=overall,
        attendance=attendance,
        assignments=completion,
        reasons=reasons,
    )


def evaluate_reasons(overall_average, attendance, completion) -> list[str]:
    """The transparent rules. Each returns a specific, checkable sentence.

    A rule stays silent when it has no data to judge on: a learner with no
    marked papers is not flagged for low marks, and a school with no attendance
    taken yet is not flagged for low attendance.
    """
    reasons = []

    if overall_average is not None and overall_average < LOW_MARK_THRESHOLD:
        reasons.append(
            f"Average mark {overall_average:.0f}% is below "
            f"{LOW_MARK_THRESHOLD:.0f}%."
        )

    if attendance.rate is not None and attendance.rate < LOW_ATTENDANCE_THRESHOLD:
        reasons.append(
            f"Attendance {attendance.rate:.0f}% over the last "
            f"{attendance.school_days} school days is below "
            f"{LOW_ATTENDANCE_THRESHOLD:.0f}%."
        )

    if completion.missed_past_due >= MISSED_ASSIGNMENT_THRESHOLD:
        reasons.append(
            f"{completion.missed_past_due} assignments past their due date with "
            f"nothing submitted."
        )

    return reasons


def _subject_averages(papers) -> list[SubjectAverage]:
    buckets = {}
    for paper in papers:
        percentage = _paper_percentage(paper)
        if percentage is None:
            continue
        buckets.setdefault(paper.memorandum.subject, []).append(percentage)

    return [
        SubjectAverage(
            subject=subject,
            average=round(sum(values) / len(values), 1),
            paper_count=len(values),
        )
        for subject, values in sorted(buckets.items())
    ]


def _overall_average(papers) -> float | None:
    percentages = [
        percentage
        for paper in papers
        if (percentage := _paper_percentage(paper)) is not None
    ]
    if not percentages:
        return None
    return round(sum(percentages) / len(percentages), 1)


def _attendance_summary(school_days, present_days, has_history) -> AttendanceSummary:
    # No basis to judge attendance — either no attendance has been taken at all,
    # or this learner has no attendance history yet (e.g. newly enrolled). Leave
    # the rate unset so no flag fires, the same "silent without data" stance the
    # marks rule takes. A learner who *has* history but was present on few recent
    # school days still gets a real 0-100% rate, and can be flagged.
    if school_days == 0 or not has_history:
        return AttendanceSummary(
            school_days=school_days, present_days=present_days, rate=None
        )
    return AttendanceSummary(
        school_days=school_days,
        present_days=present_days,
        rate=round(present_days / school_days * 100, 1),
    )


def _assignment_completion(assignments, submitted_ids) -> AssignmentCompletion:
    missed = sum(
        1
        for assignment in assignments
        if assignment.is_past_due and assignment.id not in submitted_ids
    )
    return AssignmentCompletion(
        total=len(assignments),
        submitted=len(submitted_ids),
        missed_past_due=missed,
    )


# --------------------------------------------------------------------------- #
# Bulk fetch helpers
# --------------------------------------------------------------------------- #


def _paper_percentage(paper):
    result = getattr(paper, "result", None)
    return result.percentage if result is not None else None


def _marked_papers(student_ids) -> dict:
    """{student_id: [marked papers with result + memorandum prefetched]}."""
    papers = (
        Paper.objects.filter(
            student_id__in=student_ids, status=Paper.Status.MARKED
        )
        .select_related("memorandum", "result")
        .order_by("-created_at")
    )
    grouped = {}
    for paper in papers:
        grouped.setdefault(paper.student_id, []).append(paper)
    return grouped


def _submitted_pairs(student_ids) -> set:
    """{(student_id, assignment_id)} for every submission to an assignment."""
    return set(
        Paper.objects.filter(
            student_id__in=student_ids, assignment__isnull=False
        ).values_list("student_id", "assignment_id")
    )


def _present_counts(student_ids, school_days) -> dict:
    """{student_id: days present} within the school-day window."""
    if not school_days:
        return {}
    rows = (
        Attendance.objects.filter(
            student_id__in=student_ids, date__in=school_days
        )
        .values("student_id")
        .annotate(present=Count("id"))
    )
    return {row["student_id"]: row["present"] for row in rows}


def _attendance_history_ids(student_ids) -> set:
    """The subset of learners who have any attendance record at all.

    Used to tell "present on few recent school days" (judge, and maybe flag)
    apart from "no attendance history yet" (nothing to judge, so no flag).
    """
    return set(
        Attendance.objects.filter(student_id__in=student_ids).values_list(
            "student_id", flat=True
        )
    )
