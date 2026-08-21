"""Forms for memorandum authoring and paper marking."""

from django import forms

from accounts.models import User
from accounts.scoping import students_at_school

from .models import Memorandum


def student_queryset(school):
    """Learners at ``school``, ordered by the name a teacher would look for.

    Scoped to the teacher's school (Sprint 8b): the picker only lists their own
    school's learners, and because a ModelChoiceField validates the submitted id
    against this queryset, a tampered cross-school id is rejected on POST too.
    Empty when the teacher has no school.
    """
    return students_at_school(school).order_by(
        "first_name", "last_name", "username"
    )


class StudentChoiceField(forms.ModelChoiceField):
    """A learner picker that shows names rather than usernames."""

    def label_from_instance(self, student):
        full_name = student.get_full_name()
        if full_name:
            return f"{full_name} ({student.username})"
        return student.username


class PaperUploadForm(forms.Form):
    """Pick a memorandum, attach a photo.

    A plain ``FileField`` rather than ``ImageField``: validation happens in
    ``core.images``, which needs to open the file anyway to auto-orient and
    compress it, and which produces error messages aimed at someone holding a
    phone rather than at a developer.
    """

    memorandum = forms.ModelChoiceField(
        queryset=Memorandum.objects.all(),
        empty_label="Choose a memorandum",
        help_text="The marking guide to mark this paper against.",
    )
    image = forms.FileField(
        label="Photo of the paper",
        help_text="A JPEG or PNG photo. It will be rotated and compressed automatically.",
    )


class TeacherMarkPaperForm(PaperUploadForm):
    """The teacher-facing version: the same upload, plus whose work it is.

    The learner is required here. A teacher marking a class set is not the author
    of any of it, and a mark that is not attached to a learner cannot reach them
    or their parent later, which is the entire point of the app.
    """

    student = StudentChoiceField(
        queryset=User.objects.none(),
        empty_label="Choose a learner",
        label="Whose paper is this?",
        help_text="Start typing a name to narrow the list.",
    )

    field_order = ["student", "memorandum", "image"]

    def __init__(self, *args, teacher=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Evaluated per request and scoped to the teacher's school, so a learner
        # who registered a minute ago is selectable and one at another school
        # never is.
        self.fields["student"].queryset = student_queryset(
            teacher.school if teacher is not None else None
        )
        # A teacher marks against their own memorandums. Ownership is the only
        # boundary the MVP has, and it keeps this consistent with the memorandum
        # list, which is already per-teacher.
        if teacher is not None:
            self.fields["memorandum"].queryset = Memorandum.objects.filter(
                created_by=teacher
            ).order_by("-created_at")
        self.fields["memorandum"].help_text = (
            "The marking guide for this paper. Create one first if the list is empty."
        )
        self.fields["image"].label = "Photo of the paper"
        self.fields["image"].help_text = (
            "Take the photo straight on, with the whole page in the frame. "
            "It is rotated and shrunk automatically."
        )
        self.fields["image"].widget.attrs.update(
            {"accept": "image/jpeg,image/png", "capture": "environment"}
        )
        # Picked up by static/js/isgela.js, which adds a type-to-narrow box once
        # the list is long enough to need one. Without JavaScript this is an
        # ordinary select and still works.
        self.fields["student"].widget.attrs.update(
            {"data-filterable": "true", "data-filter-label": "Type a learner's name to narrow the list"}
        )


class MemorandumForm(forms.ModelForm):
    """Teacher-facing memorandum authoring, replacing admin-only entry."""

    class Meta:
        model = Memorandum
        fields = ["title", "subject", "total_marks", "content"]
        labels = {
            "title": "What is this memorandum for?",
            "subject": "Subject",
            "total_marks": "Total marks for the whole paper",
            "content": "The marking guide",
        }
        help_texts = {
            "title": "For example: Grade 4 Mathematics Test 1, Term 3.",
            "subject": "For example Mathematics or Life Orientation. It helps the "
            "marking read subject notation, and groups this paper's marks by "
            "subject on the progress dashboard. Left blank, it files under General.",
            "total_marks": "Optional. Used only as a sanity check on the marks that come back.",
            "content": (
                "One question at a time: the question number, the answer you "
                "expect, and the marks it is worth. Plain typing is fine."
            ),
        }
        widgets = {
            "content": forms.Textarea(
                attrs={
                    "rows": 14,
                    "placeholder": (
                        "Question 1.1 (2 marks)\n"
                        "What is 7 x 6?\n"
                        "Expected answer: 42\n"
                        "\n"
                        "Question 1.2 (3 marks)\n"
                        "A shop sells pens for R5 each. What do 8 pens cost? "
                        "Show your working.\n"
                        "Expected answer: 5 x 8 = R40. One mark for the "
                        "multiplication, one for the answer, one for the rand sign."
                    ),
                }
            ),
            "title": forms.TextInput(attrs={"placeholder": "Grade 4 Mathematics Test 1"}),
            "subject": forms.TextInput(attrs={"placeholder": "Mathematics"}),
        }

    def clean_content(self):
        content = self.cleaned_data["content"].strip()
        if len(content) < 20:
            raise forms.ValidationError(
                "This looks too short to mark against. Include at least one "
                "question, the answer you expect, and the marks it is worth."
            )
        return content
