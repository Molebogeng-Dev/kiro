"""Shared domain models.

Deliberately thin for Sprint 1. The full school structure (grades, classes,
subjects, enrolment) is a later sprint; this is just the anchor those will hang
off, so that accounts already have somewhere to point.
"""

from django.db import models


class School(models.Model):
    """Placeholder for a school. Name only, by design."""

    name = models.CharField(max_length=200, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
