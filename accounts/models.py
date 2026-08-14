"""User accounts and the parent/student relationship.

iSgela has exactly three kinds of human user: a teacher, a student, and a
parent. Every account is exactly one of those, stored on ``User.role``, and
that single field drives both routing (which dashboard you land on) and
authorisation (which dashboards you are refused).
"""

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models


class Role(models.TextChoices):
    """The three roles in the teacher/student/parent loop."""

    TEACHER = "teacher", "Teacher"
    STUDENT = "student", "Student"
    PARENT = "parent", "Parent"


# Where each role goes after logging in. Kept next to Role so that adding a
# role in a later sprint is a single-place change.
ROLE_DASHBOARD_URL_NAMES = {
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
        max_length=10,
        choices=Role.choices,
        help_text="Determines which dashboard this account can reach.",
    )

    # Unique so it can become the login identifier or a notification target
    # later without a data migration to clean up duplicates.
    email = models.EmailField(unique=True)

    # Placeholder link to the stub School model. The real school/class
    # structure lands in a later sprint.
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
    def is_teacher(self) -> bool:
        return self.role == Role.TEACHER

    @property
    def is_student(self) -> bool:
        return self.role == Role.STUDENT

    @property
    def is_parent(self) -> bool:
        return self.role == Role.PARENT

    @property
    def dashboard_url_name(self) -> str | None:
        """URL name of this user's dashboard, or None if the role is unset."""
        return ROLE_DASHBOARD_URL_NAMES.get(self.role)

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
