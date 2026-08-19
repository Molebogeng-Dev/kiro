"""Registration form for all three roles."""

from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import (
    E164_VALIDATOR,
    MAX_GRADE,
    MIN_GRADE,
    ParentStudentLink,
    Role,
    User,
)

GRADE_CHOICES = [("", "Choose a grade")] + [
    (str(grade), f"Grade {grade}") for grade in range(MIN_GRADE, MAX_GRADE + 1)
]


class RegistrationForm(UserCreationForm):
    """One form, three roles, chosen by the person signing up.

    A parent may optionally supply their child's username here so the
    parent/student link exists from the moment the account is created. Every
    other way of creating that link (the admin now, a proper invite flow
    later) writes to the same table.
    """

    role = forms.ChoiceField(
        choices=Role.choices,
        help_text="Teachers, students, and parents each see a different portal.",
    )
    # Required for students, ignored for everyone else (enforced in clean()).
    # A dropdown rather than a free number so a grade outside 1-12 cannot be
    # entered, and so it reads plainly to whoever is registering.
    grade = forms.ChoiceField(
        choices=GRADE_CHOICES,
        required=False,
        label="Grade",
        help_text="Students only. This decides how attendance is taken.",
    )
    # Required for parents, ignored for everyone else (enforced in clean()).
    # This is where a WhatsApp notification will be sent, so the format is
    # checked now rather than discovered when a send fails later.
    phone_number = forms.CharField(
        required=False,
        label="WhatsApp number",
        help_text="Parents only. International format, e.g. +27821234567.",
    )
    child_username = forms.CharField(
        required=False,
        label="Your child's username (optional)",
        help_text="Parents only. Leave blank if your child has not registered yet.",
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name", "email", "role")

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()

    def clean_grade(self):
        grade = self.cleaned_data.get("grade")
        return int(grade) if grade else None

    def clean_phone_number(self):
        # Normalise (strip spaces) and validate the shape only when one was
        # given; whether it is *required* depends on the role, decided in clean().
        raw = (self.cleaned_data.get("phone_number") or "").strip().replace(" ", "")
        if raw:
            E164_VALIDATOR(raw)
        return raw

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get("role")

        # A student must have a grade; a teacher or parent never does, so any
        # grade they happened to send is discarded rather than stored.
        if role == Role.STUDENT:
            if not cleaned.get("grade"):
                self.add_error("grade", "Please choose the student's grade.")
        else:
            cleaned["grade"] = None

        # A parent must give a WhatsApp number; anyone else's is discarded.
        if role == Role.PARENT:
            if not cleaned.get("phone_number"):
                self.add_error(
                    "phone_number",
                    "A WhatsApp number is required so we can send you updates.",
                )
        else:
            cleaned["phone_number"] = ""

        return cleaned

    def clean_child_username(self):
        """Resolve the child username to a real student account."""
        username = self.cleaned_data.get("child_username", "").strip()
        if not username:
            return None

        if self.data.get("role") != Role.PARENT:
            raise forms.ValidationError(
                "Only a parent account can be linked to a learner."
            )

        try:
            child = User.objects.get(username__iexact=username)
        except User.DoesNotExist:
            raise forms.ValidationError(
                "No account with that username. Check the spelling, or leave "
                "this blank and link the learner later."
            ) from None

        if child.role != Role.STUDENT:
            raise forms.ValidationError("That username is not a student account.")

        return child

    def save(self, commit=True):
        user = super().save(commit=False)
        user.grade = self.cleaned_data.get("grade")
        # Store None rather than "" when unset, so "has a number" is a single
        # clean check everywhere.
        user.phone_number = self.cleaned_data.get("phone_number") or None

        if commit:
            user.save()
            child = self.cleaned_data.get("child_username")
            if child is not None:
                ParentStudentLink.objects.get_or_create(parent=user, student=child)

        return user
