"""Attendance and face enrollment.

One shared ``Attendance`` record represents a student's presence on a day,
whichever mechanism produced it. Primary learners (grades 1-7) get a row from a
manual roll-call; secondary learners (grades 8-12) get one from a facial
check-in, or from the manual fallback when a face will not match. Later sprints
(the parent view, the dashboard) read attendance without caring which mechanism
wrote it — that is the whole point of landing both on one model.

``FaceEnrollment`` holds the biometric side, and holds as little of it as
possible: a face descriptor (a vector of numbers face-api.js derives in the
browser), never the source photograph, and only for a secondary student whose
enrolling teacher ticked consent.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from accounts.models import Role


class Attendance(models.Model):
    """One student's presence on one day.

    At most one row per student per day, enforced by the database, so the two
    mechanisms cannot each create a record for the same learner on the same
    day. Absence is implicit: a student with no row for a day was not marked
    present, and there is no explicit "absent" state in this sprint.
    """

    class Method(models.TextChoices):
        MANUAL = "manual", "Manual roll-call"
        FACIAL = "facial", "Facial recognition"

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="attendance_records",
        limit_choices_to={"role": Role.STUDENT},
    )

    date = models.DateField(default=timezone.localdate)

    method = models.CharField(max_length=10, choices=Method.choices)

    # A primary roll-call records arrival only. For secondary learners this row
    # is a rollup of the day's AttendanceScans: arrived_at is the first scan of
    # the day, departed_at the most recent one. departed_at only becomes a true
    # "departure" retroactively, once no more scans arrive — there is no flag
    # marking a scan as the last, and none is needed.
    arrived_at = models.DateTimeField(null=True, blank=True)
    departed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["student", "date"],
                name="one_attendance_per_student_per_day",
            )
        ]
        ordering = ["-date", "student__username"]
        indexes = [models.Index(fields=["date"])]

    def __str__(self):
        return f"{self.student.username} on {self.date} ({self.get_method_display()})"

    def clean(self):
        if self.student_id and self.student.role != Role.STUDENT:
            raise ValidationError(
                {"student": "Attendance can only be recorded for a student."}
            )

    @property
    def is_present(self) -> bool:
        return self.arrived_at is not None


class AttendanceScan(models.Model):
    """One check-in event during the day.

    Secondary learners are scanned at each period change — the teacher passes
    the device round the room — so there are many of these per student per day,
    all rolling up into the single ``Attendance`` row. This is the append-only
    log of what happened; ``Attendance`` is the day's summary derived from it.

    Primary roll-call does not create these: it has no per-period concept, just
    one presence tick a day.
    """

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="attendance_scans",
        limit_choices_to={"role": Role.STUDENT},
    )
    attendance = models.ForeignKey(
        Attendance,
        on_delete=models.CASCADE,
        related_name="scans",
    )
    method = models.CharField(max_length=10, choices=Attendance.Method.choices)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attendance_scans_recorded",
        limit_choices_to={"role": Role.TEACHER},
    )
    timestamp = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["timestamp"]
        indexes = [models.Index(fields=["attendance", "timestamp"])]

    def __str__(self):
        return f"{self.student.username} scan at {self.timestamp:%H:%M} ({self.method})"


class FaceEnrollment(models.Model):
    """A secondary student's enrolled face, for check-in matching.

    One per student (re-enrolling updates it rather than adding another). The
    descriptor is the only biometric data kept — the reference photo used to
    compute it in the browser is never sent to or stored on the server.
    """

    student = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="face_enrollment",
        limit_choices_to={"role": Role.STUDENT},
    )

    # The face-api.js descriptor: a fixed-length list of floats. A JSONField so
    # it stays portable across SQLite (tests) and Postgres (Supabase).
    descriptor = models.JSONField()

    enrolled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="face_enrollments_made",
        limit_choices_to={"role": Role.TEACHER},
    )

    # Must be explicitly ticked by the enrolling teacher. Enrolling a child's
    # biometrics without recorded consent is exactly what this guards against.
    consent_confirmed = models.BooleanField(default=False)

    enrolled_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["student__username"]

    def __str__(self):
        return f"Face enrollment for {self.student.username}"

    def clean(self):
        errors = {}
        if self.student_id and not self.student.is_secondary_student:
            errors["student"] = (
                "Only secondary students (grades 8-12) can be enrolled for "
                "facial check-in."
            )
        if not self.consent_confirmed:
            errors["consent_confirmed"] = (
                "Enrollment requires the student's consent to be confirmed."
            )
        if errors:
            raise ValidationError(errors)
