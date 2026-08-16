"""Forms for posting work and material, and for submitting homework."""

from django import forms
from django.utils import timezone

from marking.models import Memorandum

from .models import Assignment, StudyMaterial


class HomeworkSubmissionForm(forms.Form):
    """A learner's homework photo.

    Only a photo. The assignment is fixed by the URL and the learner is taken
    from request.user server-side, so neither can be chosen — and neither can be
    spoofed — through this form. Validation and compression happen in
    ``marking.submissions`` / ``core.images``, which produce messages aimed at
    someone holding a phone.
    """

    image = forms.FileField(
        label="Photo of your work",
        help_text="A JPEG or PNG. Take it straight on, with the whole page in "
        "the frame. It is rotated and shrunk automatically.",
        widget=forms.ClearableFileInput(
            attrs={"accept": "image/jpeg,image/png", "capture": "environment"}
        ),
    )


class AssignmentForm(forms.ModelForm):
    class Meta:
        model = Assignment
        fields = ["title", "memorandum", "instructions", "due_date"]
        labels = {
            "title": "What is the assignment called?",
            "memorandum": "Which memorandum marks it?",
            "instructions": "What must the learners do?",
            "due_date": "When is it due?",
        }
        help_texts = {
            "title": "Learners see this first, so keep it plain.",
            "memorandum": (
                "The marking guide used when learners hand this in. "
                "Create one first if the list is empty."
            ),
            "instructions": "Write it as you would say it to the class.",
            "due_date": "Optional. Leave it blank if there is no deadline.",
        }
        widgets = {
            "title": forms.TextInput(
                attrs={"placeholder": "Multiplication practice, questions 1 to 10"}
            ),
            "instructions": forms.Textarea(
                attrs={
                    "rows": 8,
                    "placeholder": (
                        "Complete questions 1 to 10 in your workbook. Show all "
                        "your working. Photograph the page and upload it when "
                        "you are done."
                    ),
                }
            ),
            # A native date picker: far easier than typing a date, and the phone
            # keyboard becomes a calendar.
            "due_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    def __init__(self, *args, teacher=None, **kwargs):
        super().__init__(*args, **kwargs)
        # An assignment is marked against the teacher's own memorandums, matching
        # how the rest of the portal scopes ownership.
        memorandums = Memorandum.objects.order_by("-created_at")
        if teacher is not None:
            memorandums = memorandums.filter(created_by=teacher)
        self.fields["memorandum"].queryset = memorandums
        self.fields["memorandum"].empty_label = "Choose a memorandum"

    def clean_due_date(self):
        """A due date in the past is almost always a typo in the year."""
        due_date = self.cleaned_data.get("due_date")
        if due_date and due_date < timezone.localdate():
            raise forms.ValidationError(
                "That date has already passed. Check the year, or leave it blank "
                "if there is no deadline."
            )
        return due_date


class StudyMaterialForm(forms.ModelForm):
    class Meta:
        model = StudyMaterial
        fields = ["title", "content"]
        labels = {
            "title": "What is this material called?",
            "content": "The material itself",
        }
        help_texts = {
            "title": "For example: How to add fractions, step by step.",
            "content": (
                "Type or paste it in. Plain text keeps it quick to open on a "
                "phone with little data."
            ),
        }
        widgets = {
            "title": forms.TextInput(
                attrs={"placeholder": "How to add fractions, step by step"}
            ),
            "content": forms.Textarea(attrs={"rows": 16}),
        }

    def clean_content(self):
        content = self.cleaned_data["content"].strip()
        if len(content) < 20:
            raise forms.ValidationError(
                "This looks too short to be useful to a learner. Add a little more."
            )
        return content
