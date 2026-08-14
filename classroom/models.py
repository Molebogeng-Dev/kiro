"""Assignments and study materials.

Content a teacher posts for learners. Kept in its own app rather than folded into
``marking`` because none of it is marking: an assignment points at a memorandum,
but a study material has nothing to do with one, and Sprint 4 will read both from
the student side.

Both models carry the same deliberate simplification: they belong to a teacher,
not to a class, because there is no class or roster structure in the MVP. When
that structure arrives, these gain a foreign key and stop being visible to
everyone.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from accounts.models import Role


class TeacherOwnedModel(models.Model):
    """Shared bones for content a teacher posts.

    Both models below need the same four fields and the same "only a teacher may
    own this" rule, and having it in one place means the rule cannot drift
    between them.
    """

    title = models.CharField(max_length=200)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="%(class)ss",
        limit_choices_to={"role": Role.TEACHER},
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    def clean(self):
        if self.created_by_id and self.created_by.role != Role.TEACHER:
            raise ValidationError(
                {"created_by": "Only a teacher account can post this."}
            )


class Assignment(TeacherOwnedModel):
    """Work set for learners, with the memorandum it will be marked against.

    The memorandum is required rather than optional: an assignment that cannot be
    marked is just an announcement, and tying the two together at creation time
    is what lets Sprint 4 mark a homework submission without the learner
    choosing a marking guide themselves.
    """

    instructions = models.TextField(
        help_text="What the learner has to do. Plain language."
    )

    memorandum = models.ForeignKey(
        "marking.Memorandum",
        on_delete=models.PROTECT,
        related_name="assignments",
        help_text="Used to mark this assignment when learners submit it.",
    )

    due_date = models.DateField(
        null=True,
        blank=True,
        help_text="Optional. Leave blank if there is no deadline.",
    )

    class Meta(TeacherOwnedModel.Meta):
        abstract = False
        indexes = [models.Index(fields=["-created_at"])]

    @property
    def is_past_due(self) -> bool:
        if self.due_date is None:
            return False
        return self.due_date < timezone.localdate()

    @property
    def days_until_due(self):
        """Negative once overdue, None when there is no due date."""
        if self.due_date is None:
            return None
        return (self.due_date - timezone.localdate()).days


class StudyMaterial(TeacherOwnedModel):
    """Something for learners to read or revise from.

    Plain text for the MVP. No file uploads: the point of this sprint is that a
    teacher can get material to learners at all, and text costs a learner on
    metered data almost nothing to open.
    """

    content = models.TextField(
        help_text="The material itself. Plain text is fine."
    )

    class Meta(TeacherOwnedModel.Meta):
        abstract = False
        verbose_name = "study material"
        verbose_name_plural = "study materials"

    @property
    def reading_time_minutes(self) -> int:
        """Rough estimate, so a learner knows what they are opening."""
        words = len(self.content.split())
        return max(1, round(words / 200))
