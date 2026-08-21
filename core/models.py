"""Shared domain models.

The ``School`` (Sprint 8a) is the boundary the whole app has needed since
attendance: "any teacher can see any student" becomes school-scoped once every
account carries a ``school``. This sprint builds the school, its join codes, and
the teacher-invite mechanism; re-scoping the existing views to respect it is
Sprint 8b, deliberately separate.
"""

import secrets

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

# Grade bounds, kept here so School's range fields validate without importing
# from accounts (which would couple the two apps' model modules at load time).
MIN_GRADE = 1
MAX_GRADE = 12

# A deliberately unambiguous alphabet for human-typed codes: no 0/O, 1/I/L. A
# parent reading a code off a slip of paper, or a teacher typing an invite, is
# the intended user, so legibility beats raw entropy.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_JOIN_CODE_LENGTH = 6
_INVITE_CODE_LENGTH = 8


def _random_code(length: int) -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(length))


def generate_join_code() -> str:
    """A shared, reusable code students or parents type to join a school."""
    return _random_code(_JOIN_CODE_LENGTH)


class School(models.Model):
    """A school, registered by a school admin.

    A school registers with a grade range (stored as a flexible min/max, not a
    rigid primary/secondary enum) and two shared join codes generated at
    creation: one students type to register, one parents type. Both are reusable
    — many students at a school share the one student code — and neither is
    consumed by use, which is what tells them apart from a single-use
    ``TeacherInvite`` code.
    """

    name = models.CharField(max_length=200, unique=True)

    # The grade range this school covers, e.g. 1-7 (primary), 8-12 (secondary),
    # or a custom span. Stored as two integers rather than an enum so an
    # unusual range (a combined school, a grade-8-only annex) is expressible.
    min_grade = models.PositiveSmallIntegerField(
        default=MIN_GRADE,
        validators=[MinValueValidator(MIN_GRADE), MaxValueValidator(MAX_GRADE)],
    )
    max_grade = models.PositiveSmallIntegerField(
        default=MAX_GRADE,
        validators=[MinValueValidator(MIN_GRADE), MaxValueValidator(MAX_GRADE)],
    )

    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="schools_created",
        help_text="The school admin who registered this school.",
    )

    # Shared, reusable join codes. Not globally unique on purpose: a learner
    # picks their school first and then types the code, so the code only has to
    # match the chosen school, and requiring global uniqueness would only add a
    # pointless failure mode at creation. Generated once, here.
    student_join_code = models.CharField(
        max_length=12,
        default=generate_join_code,
        help_text="Shared code students type to register at this school.",
    )
    parent_student_join_code = models.CharField(
        max_length=12,
        default=generate_join_code,
        help_text="Shared code parents type to register at this school.",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def grade_range_label(self) -> str:
        """A human label like "Grades 1–7", for the dashboard and confirmations."""
        if self.min_grade == self.max_grade:
            return f"Grade {self.min_grade}"
        return f"Grades {self.min_grade}\u2013{self.max_grade}"


class TeacherInvite(models.Model):
    """A pre-listed teacher slot with a unique, single-use claim code.

    A school admin lists a teacher by name and assigned grade(s); that creates
    an invite, not an account. The teacher's real account is made later, when
    they register and claim this code — at which point ``claimed_by`` and
    ``claimed_at`` are set. A code claims exactly once: a second attempt must
    fail clearly, never silently reassign, which is enforced by the atomic
    :meth:`claim`, not by an application-level check that a race could slip past.
    """

    school = models.ForeignKey(
        School, on_delete=models.CASCADE, related_name="teacher_invites"
    )
    teacher_name = models.CharField(
        max_length=150,
        help_text="As entered by the admin, so the registering teacher can "
        "confirm this is their slot.",
    )
    assigned_grades = models.CharField(
        max_length=50,
        help_text="The grade or grade range this teacher is assigned, "
        "e.g. 'Grade 8' or 'Grades 8-12'.",
    )
    code = models.CharField(max_length=16, unique=True)
    claimed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="claimed_invite",
    )
    claimed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        state = "claimed" if self.is_claimed else "open"
        return f"{self.teacher_name} @ {self.school.name} ({state})"

    @property
    def is_claimed(self) -> bool:
        return self.claimed_by_id is not None

    @classmethod
    def create_for(cls, *, school, teacher_name, assigned_grades):
        """Create an invite with a freshly generated unique code.

        Retries on the astronomically unlikely code collision rather than
        trusting a single draw, so listing a teacher never fails spuriously.
        """
        from django.db import IntegrityError, transaction

        for _ in range(10):
            code = _random_code(_INVITE_CODE_LENGTH)
            try:
                with transaction.atomic():
                    return cls.objects.create(
                        school=school,
                        teacher_name=teacher_name,
                        assigned_grades=assigned_grades,
                        code=code,
                    )
            except IntegrityError:
                continue
        raise RuntimeError("Could not generate a unique teacher invite code.")

    @classmethod
    def claim(cls, *, school, code, user) -> bool:
        """Atomically claim the invite for ``code`` at ``school`` for ``user``.

        Returns True if this call is the one that claimed it, False if it was
        already claimed (or does not exist). The guard is a single conditional
        UPDATE — ``claimed_by__isnull=True`` in the filter — so two concurrent
        claims cannot both match the still-open row; the database serializes the
        update and exactly one sees a row-count of 1. No application-level
        read-then-write gap for a race to slip through.
        """
        claimed = cls.objects.filter(
            school=school, code=code, claimed_by__isnull=True
        ).update(claimed_by=user, claimed_at=timezone.now())
        return claimed == 1
