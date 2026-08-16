"""The one place attendance records are written.

Two paths, deliberately distinct:

* ``mark_present_manually`` is the primary roll-call: one presence tick a day,
  no per-period concept, no scans.
* ``record_scan`` is the secondary per-period path: every facial check-in *and*
  every manual fallback goes through it, appends an ``AttendanceScan``, and
  rolls the results up into the day's ``Attendance`` row. Because both methods
  share it, a failed face match is just a manual-method scan — no separate
  manual-entry logic, and no need to know whether a scan is an "arrival" or a
  "departure".
"""

from django.utils import timezone

from .models import Attendance, AttendanceScan


def mark_present_manually(student, *, when=None):
    """Record a manual presence for ``student`` today. Idempotent.

    The primary roll-call: presence, not a clock-in/out, so this only ever sets
    ``arrived_at`` and never creates a scan. Returns ``(attendance, created)``.
    """
    when = when or timezone.now()
    attendance, created = Attendance.objects.get_or_create(
        student=student,
        date=timezone.localdate(),
        defaults={"method": Attendance.Method.MANUAL, "arrived_at": when},
    )

    if not created and attendance.arrived_at is None:
        attendance.arrived_at = when
        attendance.save(update_fields=["arrived_at", "updated_at"])

    return attendance, created


def record_scan(student, *, method, recorded_by=None, when=None):
    """Record one period check-in for ``student`` today and roll it up.

    Appends an ``AttendanceScan`` and updates the day's single ``Attendance``
    row: the first scan sets ``arrived_at``, and every scan (including the
    first) moves ``departed_at`` to its own time. So ``departed_at`` is always
    "the most recent scan seen so far", which is only truly a departure once the
    day ends with no later scan — handled by the rollup, with no special flag.

    Used for both facial check-ins and the manual fallback; ``method`` records
    which this one was. Returns ``(attendance, scan, is_first_today)``.
    """
    when = when or timezone.now()
    attendance, created = Attendance.objects.get_or_create(
        student=student,
        date=timezone.localdate(),
        defaults={"method": method, "arrived_at": when, "departed_at": when},
    )

    if not created:
        if attendance.arrived_at is None:
            attendance.arrived_at = when
        attendance.departed_at = when
        attendance.save(update_fields=["arrived_at", "departed_at", "updated_at"])

    scan = AttendanceScan.objects.create(
        student=student,
        attendance=attendance,
        method=method,
        recorded_by=recorded_by,
        timestamp=when,
    )
    return attendance, scan, created
