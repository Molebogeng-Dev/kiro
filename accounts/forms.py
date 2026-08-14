"""Registration form for all three roles."""

from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import ParentStudentLink, Role, User


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
        user = super().save(commit=commit)

        child = self.cleaned_data.get("child_username")
        if commit and child is not None:
            ParentStudentLink.objects.get_or_create(parent=user, student=child)

        return user
