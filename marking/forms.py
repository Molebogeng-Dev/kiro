"""Upload form for the marking test endpoint."""

from django import forms

from .models import Memorandum


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
