"""Papers, memorandums, and marking results.

Deliberately neutral about who is submitting and why. A teacher marking a class
set of exam scripts and a learner photographing last night's homework are the
same operation as far as this app is concerned: an image, a memorandum to mark
it against, and a per-question result. Sprints 3 and 4 add the two front doors;
neither needs a different engine.
"""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from accounts.models import Role
from core.images import OUTPUT_EXTENSION
from core.storage import papers_storage

# Every memorandum has a subject, so marks can be grouped by it on the progress
# dashboard (Sprint 7). A memo written without one is filed here rather than
# left blank, which would break the grouping.
DEFAULT_SUBJECT = "General"


def normalize_subject(value) -> str:
    """Tidy a free-text subject so near-duplicates group as one.

    Strips surrounding whitespace and title-cases, so "maths" and "Maths "
    both become "Maths" and land in the same bucket on the dashboard. An empty
    or whitespace-only value becomes the default rather than a blank that would
    silently form its own group.
    """
    cleaned = (value or "").strip()
    return cleaned.title() if cleaned else DEFAULT_SUBJECT


def paper_image_path(instance, filename):
    """Bucket path for an uploaded paper.

    A UUID rather than the original filename: phone cameras produce colliding
    names like IMG_0001.jpg, and the original name is learner-supplied input we
    would otherwise be putting into a storage path.
    """
    return f"papers/{timezone.now():%Y/%m}/{uuid.uuid4().hex}.{OUTPUT_EXTENSION}"


class Memorandum(models.Model):
    """The marking guide a paper is judged against.

    Plain text on purpose for this sprint. A teacher types or pastes the memo
    into the admin, and it is handed to the model verbatim. Structured
    question-by-question authoring is a Sprint 3 concern; forcing that shape now
    would mean guessing at how teachers actually write memos.
    """

    title = models.CharField(max_length=200)

    subject = models.CharField(
        max_length=100,
        blank=True,
        default=DEFAULT_SUBJECT,
        help_text="Included in the marking prompt (it helps the model read "
        "subject notation) and used to group marks by subject on the progress "
        "dashboard. Normalized on save; defaults to General if left blank.",
    )

    content = models.TextField(
        help_text="The marking guide: each question, the expected answer, and "
        "the marks available. Plain text is fine.",
    )

    total_marks = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Optional. Used only to sanity-check what the model returns.",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="memorandums",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        # Normalize here rather than only in the form so every path — admin,
        # transcription, tests, a future import — files marks under a tidy,
        # groupable subject.
        self.subject = normalize_subject(self.subject)
        super().save(*args, **kwargs)


class Paper(models.Model):
    """One uploaded submission awaiting, or having been through, marking."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        MARKED = "marked", "Marked"
        FAILED = "failed", "Failed"

    class FailureKind(models.TextChoices):
        """Why marking failed.

        Kept distinct because the operational response differs: a rate limit
        means wait and retry, an invalid response means the prompt or the model
        needs attention, and an image problem means asking the submitter for a
        better photo. Collapsing them into one error string would hide exactly
        the signal we need on a free tier.
        """

        RATE_LIMITED = "rate_limited", "Rate limited by the AI provider"
        NO_CREDIT = "no_credit", "AI provider requires credit"
        MODEL_UNAVAILABLE = "model_unavailable", "Model unavailable"
        SERVICE_ERROR = "service_error", "AI provider error"
        INVALID_RESPONSE = "invalid_response", "AI response could not be parsed"
        IMAGE_ERROR = "image_error", "Image could not be processed"

    memorandum = models.ForeignKey(
        Memorandum,
        on_delete=models.PROTECT,
        related_name="papers",
        help_text="Deleting a memorandum that has been marked against would "
        "orphan the results, so it is protected.",
    )

    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="papers",
    )

    # Whose work this is, as opposed to who uploaded it. A teacher marking a
    # class set is not the author of any of it, and the result has to reach the
    # right learner and later the right parent. Nullable because a paper can
    # exist before anyone says whose it is, and because Sprint 2's endpoint
    # predates this field.
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="papers_as_student",
        null=True,
        blank=True,
        limit_choices_to={"role": Role.STUDENT},
        help_text="The learner whose work this is.",
    )

    # The assignment this paper answers, when a learner submitted it for one.
    # Distinct from the memorandum: the memorandum is how it is marked, the
    # assignment is what it responds to, and the student portal needs the latter
    # to show "submitted / not yet" per assignment. Nullable because a teacher
    # marking a loose exam script is not answering any assignment. SET_NULL, not
    # CASCADE: deleting an assignment must never delete a learner's marked work.
    # A string reference avoids an import cycle (classroom already points here).
    assignment = models.ForeignKey(
        "classroom.Assignment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submissions",
        help_text="The assignment this paper answers, if it was submitted for one.",
    )

    image = models.ImageField(upload_to=paper_image_path, storage=papers_storage)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )

    failure_kind = models.CharField(
        max_length=20, choices=FailureKind.choices, blank=True
    )
    failure_detail = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self):
        return f"Paper {self.pk} ({self.get_status_display()})"

    def clean(self):
        """Only a teacher or a learner submits work.

        A parent account has no reason to upload a paper, and letting one do so
        would put a parent's submission into a learner's record.
        """
        errors = {}

        if self.submitted_by_id and self.submitted_by.role not in {
            Role.TEACHER,
            Role.STUDENT,
        }:
            errors["submitted_by"] = "Only teachers and students can submit papers."

        if self.student_id and self.student.role != Role.STUDENT:
            errors["student"] = "A paper can only belong to a student account."

        if errors:
            raise ValidationError(errors)

    @property
    def belongs_to_display(self) -> str:
        """Whose work this is, for display. Falls back to the uploader."""
        owner = self.student or self.submitted_by
        return owner.get_full_name() or owner.username

    def record_failure(self, kind, detail):
        """Store why marking failed, keeping the submission itself intact."""
        self.status = self.Status.FAILED
        self.failure_kind = kind
        self.failure_detail = str(detail)[:2000]
        self.save(update_fields=["status", "failure_kind", "failure_detail", "updated_at"])

    @property
    def failure_message(self) -> str:
        """What to tell the person looking at the screen.

        Deliberately free of provider names, status codes, and the word "API".
        A teacher needs to know whether to try again, take a better photo, or
        fetch someone technical, and nothing else.
        """
        return FAILURE_ADVICE.get(
            self.failure_kind,
            "Marking did not finish. You can try again.",
        )

    @property
    def is_retryable(self) -> bool:
        """Whether trying again might plausibly work.

        A withdrawn model or an empty account will fail identically until
        somebody changes a setting, so the button is not offered there.
        """
        return self.failure_kind not in {
            self.FailureKind.NO_CREDIT,
            self.FailureKind.MODEL_UNAVAILABLE,
        }


# Plain-language advice per failure kind, for the person holding the phone.
# Defined after Paper so it can use the choices, and read at call time by
# Paper.failure_message.
FAILURE_ADVICE = {
    Paper.FailureKind.RATE_LIMITED: (
        "The marking service is busy at the moment. Wait a minute, then try again."
    ),
    Paper.FailureKind.NO_CREDIT: (
        "The marking service has run out of credit. Whoever set up iSgela for "
        "your school needs to top it up before marking will work."
    ),
    Paper.FailureKind.MODEL_UNAVAILABLE: (
        "The marking service is unavailable. This needs a settings change, so "
        "trying again will not help — please report it."
    ),
    Paper.FailureKind.SERVICE_ERROR: (
        "The marking service did not answer in time. This is usually temporary, "
        "so please try again."
    ),
    Paper.FailureKind.INVALID_RESPONSE: (
        "Marking came back in a form we could not read. Trying again often "
        "works, and a clearer, straighter photo helps most."
    ),
    Paper.FailureKind.IMAGE_ERROR: (
        "That photo could not be read. Take another one in better light, with "
        "the whole page in the frame."
    ),
}


class MarkingResult(models.Model):
    """What the model made of one paper."""

    paper = models.OneToOneField(Paper, on_delete=models.CASCADE, related_name="result")

    marks_awarded = models.DecimalField(
        max_digits=7, decimal_places=2, validators=[MinValueValidator(0)]
    )
    marks_available = models.DecimalField(
        max_digits=7, decimal_places=2, validators=[MinValueValidator(0)]
    )

    summary = models.TextField(
        blank=True,
        help_text="Plain-language overview. Sprint 7 sends this to parents.",
    )

    model_used = models.CharField(
        max_length=120,
        help_text="Which model produced this. Results are not comparable "
        "across models, so it is recorded per result rather than assumed.",
    )

    raw_response = models.TextField(
        blank=True,
        help_text="The model's unparsed reply, kept for debugging drift.",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.marks_awarded}/{self.marks_available} for paper {self.paper_id}"

    @property
    def percentage(self):
        if not self.marks_available:
            return None
        return round(float(self.marks_awarded) / float(self.marks_available) * 100, 1)


class QuestionResult(models.Model):
    """One question's outcome.

    A row per question rather than a JSON blob, because the point of iSgela is
    noticing that a learner keeps losing marks on the same kind of question.
    That is a query, and queries want columns.
    """

    result = models.ForeignKey(
        MarkingResult, on_delete=models.CASCADE, related_name="questions"
    )

    # Text, not a number: real papers number questions "1.2", "3(b)", "4.1.2".
    number = models.CharField(max_length=20)

    marks_awarded = models.DecimalField(
        max_digits=6, decimal_places=2, validators=[MinValueValidator(0)]
    )
    marks_available = models.DecimalField(
        max_digits=6, decimal_places=2, validators=[MinValueValidator(0)]
    )

    feedback = models.TextField(
        help_text="Why marks were lost and what to do about it, not just that "
        "the answer was wrong."
    )

    position = models.PositiveSmallIntegerField(
        default=0, help_text="Order the model returned them in."
    )

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return f"Q{self.number}: {self.marks_awarded}/{self.marks_available}"

    @property
    def marks_lost(self):
        return self.marks_available - self.marks_awarded

    @property
    def is_full_marks(self):
        return self.marks_awarded == self.marks_available
