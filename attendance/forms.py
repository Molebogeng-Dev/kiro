"""Forms for enrollment and the manual fallback.

The roll-call itself is a plain list of checkboxes read straight from the POST
in the view, so it needs no form class. These two forms carry the choices that
must be constrained: enrollment and the fallback both act on secondary students
only, and enrollment must not proceed without consent and a captured descriptor.
"""

import json

from django import forms

from accounts.models import (
    PRIMARY_GRADE_MAX,
    SECONDARY_GRADE_MIN,
    Role,
    User,
)

from .matching import InvalidDescriptor, parse_descriptor


class StudentChoiceField(forms.ModelChoiceField):
    """Shows a learner's name and grade rather than their username."""

    def label_from_instance(self, student):
        name = student.get_full_name() or student.username
        return f"{name} — Grade {student.grade}"


def primary_students():
    return User.objects.filter(
        role=Role.STUDENT, grade__lte=PRIMARY_GRADE_MAX
    ).order_by("grade", "first_name", "last_name", "username")


def secondary_students():
    return User.objects.filter(
        role=Role.STUDENT, grade__gte=SECONDARY_GRADE_MIN
    ).order_by("grade", "first_name", "last_name", "username")


class FaceEnrollmentForm(forms.Form):
    """Enroll a secondary student's face, with consent, storing only a vector."""

    student = StudentChoiceField(
        queryset=secondary_students(),
        empty_label="Choose a student",
        label="Which student?",
        help_text="Only grades 8 to 12 use facial check-in.",
    )
    consent_confirmed = forms.BooleanField(
        required=True,
        label="I confirm this student has consented to facial check-in",
        error_messages={
            "required": "You must confirm consent before enrolling a student's face."
        },
    )
    # Filled in by the browser after capturing a photo and computing the
    # descriptor. The photo itself never leaves the browser.
    descriptor = forms.CharField(widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Re-evaluate per request so a student who registered moments ago is
        # already selectable.
        self.fields["student"].queryset = secondary_students()

    def clean_descriptor(self):
        raw = self.cleaned_data.get("descriptor", "").strip()
        if not raw:
            raise forms.ValidationError(
                "No face was captured. Start the camera and take a photo first."
            )
        try:
            return parse_descriptor(json.loads(raw))
        except (json.JSONDecodeError, InvalidDescriptor) as exc:
            raise forms.ValidationError(
                f"That face capture could not be read: {exc}"
            ) from None


class FallbackMarkForm(forms.Form):
    """The manual fallback when a secondary student's face will not match."""

    student = StudentChoiceField(
        queryset=secondary_students(),
        empty_label="Choose a student",
        label="Mark present by hand",
        help_text="Use this only when the camera cannot recognise the student.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["student"].queryset = secondary_students()
