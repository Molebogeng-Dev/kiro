"""Records of parent notifications about marked papers.

One row per (paper, parent), enforced by the database. That single constraint
does double duty: it is the audit record of what was sent, and it is the dedupe
guard — re-marking a paper cannot notify the same parent twice, because the
second attempt collides with the existing row.

A notification records what was *attempted*, whether or not it arrived: a failed
WhatsApp send is a row with ``status="failed"`` and the error, never a silent
gap. This is deliberately decoupled from the paper — the paper is marked either
way, and the teacher and student see their result regardless of what happens
here.
"""

from django.conf import settings
from django.db import models

from accounts.models import Role


class Notification(models.Model):
    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    paper = models.ForeignKey(
        "marking.Paper",
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    parent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
        limit_choices_to={"role": Role.PARENT},
    )

    # The plain-language message that was sent (or attempted). Kept even on
    # failure, so a retry of the send has the text and does not regenerate it.
    summary_text = models.TextField()

    status = models.CharField(max_length=10, choices=Status.choices)
    error = models.TextField(blank=True, default="")

    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["paper", "parent"],
                name="one_notification_per_parent_per_paper",
            )
        ]
        ordering = ["-sent_at"]

    def __str__(self):
        return f"Notification to {self.parent.username} about paper {self.paper_id} ({self.status})"
