"""The teacher progress dashboard (Sprint 7).

Two things are under test. The aggregation: marks group by subject and average
correctly, attendance is measured over the recent school-day window, and
assignment completion counts submissions against what was set. And the flag: the
transparent threshold rules trigger on the cases that should and stay quiet on
the cases that should not, with the boundary values pinned down so a later tweak
to a threshold is a visible, deliberate change.

Access mirrors attendance, not marking ownership: *any* teacher can view *any*
learner's rollup, and non-teachers are refused. These live in their own module
(not the Sprint 2 ``core/tests.py``) so the run.sh sprint mapping keeps them
under Sprint 7.
"""

from datetime import timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from attendance.models import Attendance
from classroom.models import Assignment
from core.progress import (
    LOW_ATTENDANCE_THRESHOLD,
    LOW_MARK_THRESHOLD,
    AssignmentCompletion,
    AttendanceSummary,
    build_student_rollup,
    evaluate_reasons,
)
from marking.models import MarkingResult, Memorandum, Paper

from notifications.tests.support import make_parent, make_student, make_teacher


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def marked_paper(student, teacher, subject, awarded, available=100):
    """A marked paper worth ``awarded``/``available`` in ``subject``."""
    memo = Memorandum.objects.create(
        title=f"{subject} test",
        subject=subject,
        content="Question 1 (marks). Expected answer.",
        total_marks=available,
        created_by=teacher,
    )
    paper = Paper.objects.create(
        memorandum=memo,
        submitted_by=teacher,
        student=student,
        image=SimpleUploadedFile("p.jpg", b"stub", "image/jpeg"),
        status=Paper.Status.MARKED,
    )
    MarkingResult.objects.create(
        paper=paper,
        marks_awarded=Decimal(str(awarded)),
        marks_available=Decimal(str(available)),
        summary="",
        model_used="test",
        raw_response="{}",
    )
    return paper


def present_on(student, day, method=Attendance.Method.MANUAL):
    return Attendance.objects.create(
        student=student, date=day, method=method, arrived_at=timezone.now()
    )


def make_assignment(teacher, *, due, memo=None):
    memo = memo or Memorandum.objects.create(
        title="Assignment memo", content="Q1 (2).", total_marks=10, created_by=teacher
    )
    return Assignment.objects.create(
        title="A task",
        instructions="Do the work.",
        memorandum=memo,
        due_date=due,
        created_by=teacher,
    )


def submit(student, assignment):
    return Paper.objects.create(
        memorandum=assignment.memorandum,
        submitted_by=student,
        student=student,
        assignment=assignment,
        image=SimpleUploadedFile("p.jpg", b"stub", "image/jpeg"),
        status=Paper.Status.MARKED,
    )


def recent_days(n):
    """The last ``n`` calendar days, newest first (test school-day fixtures)."""
    today = timezone.localdate()
    return [today - timedelta(days=offset) for offset in range(n)]


# --------------------------------------------------------------------------- #
# Access control
# --------------------------------------------------------------------------- #


class ProgressAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = make_teacher("prog-teacher")
        cls.other_teacher = make_teacher("prog-teacher-2")
        cls.student = make_student("prog-student")
        cls.parent = make_parent("prog-parent")

    def _urls(self):
        return [
            reverse("core:progress_dashboard"),
            reverse("core:progress_student", args=[self.student.id]),
        ]

    def test_a_teacher_reaches_the_list_and_a_rollup(self):
        self.client.force_login(self.teacher)
        for url in self._urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_any_teacher_can_view_any_learner_not_only_one(self):
        # The attendance-style boundary: a teacher who marked nothing for this
        # learner still sees their rollup.
        self.client.force_login(self.other_teacher)
        response = self.client.get(
            reverse("core:progress_student", args=[self.student.id])
        )
        self.assertEqual(response.status_code, 200)

    def test_a_student_is_refused(self):
        self.client.force_login(self.student)
        for url in self._urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_a_parent_is_refused(self):
        self.client.force_login(self.parent)
        for url in self._urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_an_anonymous_visitor_is_sent_to_login(self):
        response = self.client.get(reverse("core:progress_dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.headers["Location"])

    def test_a_non_student_id_is_a_404(self):
        self.client.force_login(self.teacher)
        response = self.client.get(
            reverse("core:progress_student", args=[self.teacher.id])
        )
        self.assertEqual(response.status_code, 404)


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #


class MarkAggregationTests(TestCase):
    def setUp(self):
        self.teacher = make_teacher("mark-teacher")
        self.student = make_student("mark-student")

    def test_marks_group_by_subject_and_average(self):
        marked_paper(self.student, self.teacher, "Mathematics", 80)
        marked_paper(self.student, self.teacher, "Mathematics", 60)
        marked_paper(self.student, self.teacher, "English", 50)

        rollup = build_student_rollup(self.student)
        by_subject = {s.subject: s for s in rollup.subjects}

        self.assertEqual(by_subject["Mathematics"].average, 70.0)
        self.assertEqual(by_subject["Mathematics"].paper_count, 2)
        self.assertEqual(by_subject["English"].average, 50.0)
        # Overall is the mean across every marked paper.
        self.assertEqual(rollup.overall_average, 63.3)

    def test_only_marked_papers_count(self):
        marked_paper(self.student, self.teacher, "Mathematics", 80)
        # A failed paper has no result and must not drag the average.
        Paper.objects.create(
            memorandum=Memorandum.objects.create(
                title="failed memo", subject="Mathematics", content="Q.",
                created_by=self.teacher,
            ),
            submitted_by=self.teacher,
            student=self.student,
            image=SimpleUploadedFile("p.jpg", b"stub", "image/jpeg"),
            status=Paper.Status.FAILED,
        )

        rollup = build_student_rollup(self.student)
        self.assertEqual(rollup.overall_average, 80.0)
        self.assertEqual(len(rollup.subjects), 1)

    def test_a_learner_with_no_marks_has_no_average(self):
        rollup = build_student_rollup(self.student)
        self.assertIsNone(rollup.overall_average)
        self.assertEqual(rollup.subjects, [])


class AttendanceAggregationTests(TestCase):
    def setUp(self):
        self.teacher = make_teacher("att-teacher")
        self.student = make_student("att-student")
        self.anchor = make_student("att-anchor")

    def test_rate_is_measured_over_recent_school_days(self):
        days = recent_days(10)
        # The anchor establishes all ten days as days attendance was taken.
        for day in days:
            present_on(self.anchor, day)
        # The learner was present on seven of them.
        for day in days[:7]:
            present_on(self.student, day)

        rollup = build_student_rollup(self.student)

        self.assertEqual(rollup.attendance.school_days, 10)
        self.assertEqual(rollup.attendance.present_days, 7)
        self.assertEqual(rollup.attendance.rate, 70.0)

    def test_no_attendance_yet_means_no_rate(self):
        rollup = build_student_rollup(self.student)
        self.assertIsNone(rollup.attendance.rate)
        self.assertEqual(rollup.attendance.school_days, 0)

    def test_a_learner_with_no_history_is_not_judged_on_attendance(self):
        # School days exist (the anchor sets them), but this learner has no
        # attendance history of their own — a newly enrolled learner should not
        # read as 0% and get flagged. No rate, no flag.
        for day in recent_days(10):
            present_on(self.anchor, day)

        rollup = build_student_rollup(self.student)

        self.assertIsNone(rollup.attendance.rate)
        self.assertFalse(
            any("Attendance" in reason for reason in rollup.reasons)
        )

    def test_a_learner_who_stopped_attending_is_still_flagged(self):
        # Has history (present on older days), but absent across the recent
        # window → a real 0-ish rate that should still flag.
        days = recent_days(10)
        for day in days:
            present_on(self.anchor, day)
        # The learner was present only on the oldest recorded day.
        present_on(self.student, days[-1])

        rollup = build_student_rollup(self.student)

        self.assertIsNotNone(rollup.attendance.rate)
        self.assertTrue(rollup.attendance.rate < 80.0)
        self.assertTrue(any("Attendance" in reason for reason in rollup.reasons))


class AssignmentCompletionTests(TestCase):
    def setUp(self):
        self.teacher = make_teacher("asg-teacher")
        self.student = make_student("asg-student")

    def test_completion_counts_submissions_against_all_assignments(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        tomorrow = timezone.localdate() + timedelta(days=1)
        overdue_a = make_assignment(self.teacher, due=yesterday)
        make_assignment(self.teacher, due=yesterday)  # overdue, not submitted
        upcoming = make_assignment(self.teacher, due=tomorrow)

        submit(self.student, overdue_a)
        submit(self.student, upcoming)

        rollup = build_student_rollup(self.student)

        self.assertEqual(rollup.assignments.total, 3)
        self.assertEqual(rollup.assignments.submitted, 2)
        # Only the one overdue assignment with nothing submitted counts as missed.
        self.assertEqual(rollup.assignments.missed_past_due, 1)


# --------------------------------------------------------------------------- #
# The flag rules — boundaries pinned down
# --------------------------------------------------------------------------- #


def _ok_attendance():
    return AttendanceSummary(school_days=10, present_days=10, rate=100.0)


def _no_missed():
    return AssignmentCompletion(total=0, submitted=0, missed_past_due=0)


class FlagThresholdTests(TestCase):
    """Pure threshold logic, so boundaries are unambiguous."""

    def test_low_average_flags_below_but_not_at_the_threshold(self):
        below = evaluate_reasons(49.0, _ok_attendance(), _no_missed())
        at = evaluate_reasons(LOW_MARK_THRESHOLD, _ok_attendance(), _no_missed())

        self.assertTrue(any("Average mark" in reason for reason in below))
        self.assertEqual(at, [])

    def test_missing_average_is_never_a_reason(self):
        # A learner with no marks yet is not flagged for low marks.
        self.assertEqual(evaluate_reasons(None, _ok_attendance(), _no_missed()), [])

    def test_low_attendance_flags_below_but_not_at_the_threshold(self):
        below = evaluate_reasons(
            70.0, AttendanceSummary(10, 7, 70.0), _no_missed()
        )
        at = evaluate_reasons(
            70.0, AttendanceSummary(10, 8, LOW_ATTENDANCE_THRESHOLD), _no_missed()
        )
        # 70 average would flag marks; isolate the attendance reason.
        self.assertTrue(any("Attendance" in reason for reason in below))
        self.assertFalse(any("Attendance" in reason for reason in at))

    def test_no_attendance_data_is_never_a_reason(self):
        reasons = evaluate_reasons(80.0, AttendanceSummary(0, 0, None), _no_missed())
        self.assertEqual(reasons, [])

    def test_two_missed_assignments_flag_but_one_does_not(self):
        two = evaluate_reasons(80.0, _ok_attendance(), AssignmentCompletion(5, 3, 2))
        one = evaluate_reasons(80.0, _ok_attendance(), AssignmentCompletion(5, 4, 1))

        self.assertTrue(any("past their due date" in reason for reason in two))
        self.assertEqual(one, [])

    def test_a_healthy_learner_has_no_reasons(self):
        self.assertEqual(
            evaluate_reasons(75.0, _ok_attendance(), _no_missed()), []
        )

    def test_several_problems_produce_several_reasons(self):
        reasons = evaluate_reasons(
            30.0, AttendanceSummary(10, 5, 50.0), AssignmentCompletion(5, 1, 3)
        )
        self.assertEqual(len(reasons), 3)


class FlagIntegrationTests(TestCase):
    """The flag as it actually reaches a teacher, end to end."""

    def setUp(self):
        self.teacher = make_teacher("flag-teacher")

    def test_a_struggling_learner_is_flagged_with_a_visible_reason(self):
        student = make_student("struggling")
        marked_paper(student, self.teacher, "Mathematics", 35)

        self.client.force_login(self.teacher)
        response = self.client.get(
            reverse("core:progress_student", args=[student.id])
        )

        self.assertContains(response, "Needs attention")
        self.assertContains(response, "below 50%")

    def test_a_healthy_learner_shows_as_on_track(self):
        student = make_student("thriving")
        marked_paper(student, self.teacher, "Mathematics", 82)

        self.client.force_login(self.teacher)
        response = self.client.get(
            reverse("core:progress_student", args=[student.id])
        )

        self.assertContains(response, "On track")

    def test_the_list_shows_a_flagged_learner(self):
        student = make_student("listed")
        marked_paper(student, self.teacher, "Mathematics", 20)

        self.client.force_login(self.teacher)
        response = self.client.get(reverse("core:progress_dashboard"))

        self.assertContains(response, "listed")
        self.assertContains(response, "Needs attention")
