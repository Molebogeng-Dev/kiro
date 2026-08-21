"""User accounts and the parent/student relationship.

iSgela has exactly three kinds of human user: a teacher, a student, and a
parent. Every account is exactly one of those, stored on ``User.role``, and
that single field drives both routing (which dashboard you land on) and
authorisation (which dashboards you are refused).
"""

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import (
    MaxValueValidator,
    MinValueValidator,
    RegexValidator,
)
from django.db import models

# E.164: a leading +, a non-zero country-code digit, then up to 14 more digits.
# This is the shape WhatsApp/Twilio expect, so validating it at the door means a
# bad number is caught at registration, not when a notification silently fails.
E164_VALIDATOR = RegexValidator(
    regex=r"^\+[1-9]\d{7,14}$",
    message="Enter a phone number in international format, for example +27821234567.",
)


class Role(models.TextChoices):
    """The roles in iSgela.

    The teacher/student/parent loop is the original three. ``school_admin``
    (Sprint 8a) registers a school and its teachers, and is the role that makes
    ``User.school`` meaningful for everyone else.
    """

    SCHOOL_ADMIN = "school_admin", "School admin"
    TEACHER = "teacher", "Teacher"
    STUDENT = "student", "Student"
    PARENT = "parent", "Parent"


# The two grade bands. Attendance (Sprint 5) treats them differently: primary
# learners are marked by a manual roll-call, secondary learners by facial
# check-in. Kept here, next to Role, because "which band is this learner in"
# is an account fact the whole app reasons about, not an attendance detail.
PRIMARY_GRADE_MAX = 7
SECONDARY_GRADE_MIN = 8
MIN_GRADE = 1
MAX_GRADE = 12


# Where each role goes after logging in. Kept next to Role so that adding a
# role in a later sprint is a single-place change.
ROLE_DASHBOARD_URL_NAMES = {
    Role.SCHOOL_ADMIN: "core:school_admin_dashboard",
    Role.TEACHER: "core:teacher_dashboard",
    Role.STUDENT: "core:student_dashboard",
    Role.PARENT: "core:parent_dashboard",
}


class User(AbstractUser):
    """A single account, with exactly one role.

    Kept as a custom model from day one because swapping the user model later
    in a Django project is painful, and because every feature in the later
    sprints (marking, attendance, notifications) needs to know who it is
    talking to.
    """

    role = models.CharField(
        # Wide enough for the longest role value ("school_admin").
        max_length=20,
        choices=Role.choices,
        help_text="Determines which dashboard this account can reach.",
    )

    # Unique so it can become the login identifier or a notification target
    # later without a data migration to clean up duplicates.
    email = models.EmailField(unique=True)

    # Only meaningful for students; null for teachers and parents. Required at
    # student registration by the form, not the database, so a teacher/parent
    # row stays clean with no grade. The value decides which attendance flow a
    # student belongs to (grades 1-7 manual, 8-12 facial).
    grade = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(MIN_GRADE), MaxValueValidator(MAX_GRADE)],
        help_text="1 to 12. Only set for students.",
    )

    # Only meaningful for parents; null for teachers and students. Required at
    # parent registration by the form, not the database (same pattern as grade),
    # and stored in E.164 so it is ready to become a WhatsApp address without
    # reformatting. Notifications only ever go to a parent whose number is set.
    phone_number = models.CharField(
        max_length=16,
        null=True,
        blank=True,
        validators=[E164_VALIDATOR],
        help_text="International format, e.g. +27821234567. Only set for parents.",
    )

    # The school this account belongs to. Set at registration (Sprint 8a) for
    # every role — including the school_admin, who points at the school they
    # just created — so "which school is this user in?" is one query,
    # ``request.user.school``, regardless of role. Nullable so pre-Sprint-8a
    # accounts and superusers stay valid; the registration flows require it.
    school = models.ForeignKey(
        "core.School",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users",
    )

    # Prompted for by createsuperuser, so an admin account can never be
    # created without a valid role and a usable email address.
    REQUIRED_FIELDS = ["email", "role"]

    class Meta:
        ordering = ["username"]

    def __str__(self):
        label = self.get_full_name() or self.username
        return f"{label} ({self.get_role_display() or 'no role'})"

    @property
    def is_school_admin(self) -> bool:
        return self.role == Role.SCHOOL_ADMIN

    @property
    def is_teacher(self) -> bool:
        return self.role == Role.TEACHER

    @property
    def is_student(self) -> bool:
        return self.role == Role.STUDENT

    @property
    def is_parent(self) -> bool:
        return self.role == Role.PARENT

    @property
    def is_primary_student(self) -> bool:
        """A student in grades 1-7 (manual roll-call band)."""
        return (
            self.role == Role.STUDENT
            and self.grade is not None
            and self.grade <= PRIMARY_GRADE_MAX
        )

    @property
    def is_secondary_student(self) -> bool:
        """A student in grades 8-12 (facial check-in band)."""
        return (
            self.role == Role.STUDENT
            and self.grade is not None
            and self.grade >= SECONDARY_GRADE_MIN
        )

    @property
    def dashboard_url_name(self) -> str | None:
        """URL name of this user's dashboard, or None if the role is unset."""
        return ROLE_DASHBOARD_URL_NAMES.get(self.role)

    @property
    def whatsapp_address(self) -> str | None:
        """This user's number as a Twilio WhatsApp address, or None."""
        return f"whatsapp:{self.phone_number}" if self.phone_number else None

    @property
    def parents(self):
        """Parent accounts linked to this student."""
        return User.objects.filter(student_links__student=self)

    @property
    def children(self):
        """Student accounts linked to this parent."""
        return User.objects.filter(parent_links__parent=self)


class ParentStudentLink(models.Model):
    """Connects one student to one parent. A student may have several.

    This is the join later sprints depend on: when a homework submission is
    marked or an absence is recorded, this table answers "who needs to hear
    about it?".

    Modelled explicitly rather than as a plain ManyToMany so that per-link
    detail (preferred notification channel, primary contact, relationship
    type) can be added later without restructuring.
    """

    parent = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="student_links",
        limit_choices_to={"role": Role.PARENT},
    )
    student = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="parent_links",
        limit_choices_to={"role": Role.STUDENT},
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["parent", "student"],
                name="unique_parent_student_link",
            )
        ]
        ordering = ["student__username", "parent__username"]

    def __str__(self):
        return f"{self.parent.username} -> {self.student.username}"

    def clean(self):
        """Guard the roles on both sides of the link.

        The database cannot express "the parent side must be a parent
        account", so it is enforced here and exercised by the admin, the
        registration form, and the tests.
        """
        errors = {}
        if self.parent_id and self.parent.role != Role.PARENT:
            errors["parent"] = "The parent side of this link must be a parent account."
        if self.student_id and self.student.role != Role.STUDENT:
            errors["student"] = (
                "The student side of this link must be a student account."
            )
        if errors:
            raise ValidationError(errors)
