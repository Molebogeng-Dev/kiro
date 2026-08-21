"""Registration form for all three roles."""

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.db import transaction

from core.models import MAX_GRADE as SCHOOL_MAX_GRADE
from core.models import MIN_GRADE as SCHOOL_MIN_GRADE
from core.models import School, TeacherInvite

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

# The two quick presets offered at school registration, plus an escape hatch for
# anything else. The range is still stored as a flexible min/max on the School.
GRADE_PRESETS = {
    "primary": (1, 7),
    "secondary": (8, 12),
}
PRESET_CHOICES = [
    ("primary", "Primary (grades 1\u20137)"),
    ("secondary", "Secondary (grades 8\u201312)"),
    ("custom", "Custom range"),
]

# Only teachers, students, and parents self-register through the main form. A
# school_admin is created through the distinct school-registration flow, so it
# is deliberately not an option here.
SELF_REGISTER_ROLE_CHOICES = [
    (Role.TEACHER.value, Role.TEACHER.label),
    (Role.STUDENT.value, Role.STUDENT.label),
    (Role.PARENT.value, Role.PARENT.label),
]


class RegistrationError(Exception):
    """A registration passed validation but could not be committed atomically.

    Raised when a teacher invite is claimed by someone else in the gap between
    validating the form and saving it. The view turns this into a form error;
    the surrounding transaction has already rolled back, so no half-registered
    account survives.
    """


class RegistrationForm(UserCreationForm):
    """One form, three roles, chosen by the person signing up.

    A parent may optionally supply their child's username here so the
    parent/student link exists from the moment the account is created. Every
    other way of creating that link (the admin now, a proper invite flow
    later) writes to the same table.
    """

    role = forms.ChoiceField(
        choices=SELF_REGISTER_ROLE_CHOICES,
        help_text="Teachers, students, and parents each see a different portal.",
    )
    # Every self-registered account joins an existing school. A school_admin
    # creates the school through the separate school-registration flow.
    school = forms.ModelChoiceField(
        queryset=School.objects.all(),
        label="School",
        help_text="Select the school you are joining.",
    )
    # One field, three meanings, resolved by role in clean(): a teacher's
    # personal single-use invite code, or the school's shared student/parent
    # join code.
    code = forms.CharField(
        label="Registration code",
        help_text="Teachers: your personal invite code. Students and parents: "
        "your school's join code.",
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

    def clean_code(self):
        # Codes are generated from an uppercase alphabet; normalise input so a
        # lowercase or padded entry still matches what is stored.
        return (self.cleaned_data.get("code") or "").strip().upper()

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get("role")

        # A student must have a grade; a teacher or parent never does, so any
        # grade they happened to send is discarded rather than stored.
        if role == Role.STUDENT:
            grade = cleaned.get("grade")
            school = cleaned.get("school")
            if not grade:
                self.add_error("grade", "Please choose the student's grade.")
            elif school is not None and not (
                school.min_grade <= grade <= school.max_grade
            ):
                # Sprint 8b: don't let a grade-3 learner register at a
                # secondary-only school. Caught here, not discovered later.
                self.add_error(
                    "grade",
                    f"{school.name} covers grades {school.min_grade} to "
                    f"{school.max_grade}. Choose a grade in that range, or check "
                    f"you picked the right school.",
                )
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

        # Validate the registration code against the selected school, in a
        # role-specific way. Guard on all three being present so a missing
        # school or an invalid role surfaces its own field error first.
        school = cleaned.get("school")
        code = cleaned.get("code")
        if role and school and code:
            self._validate_school_code(role, school, code)

        return cleaned

    def _validate_school_code(self, role, school, code):
        """Check ``code`` is right for ``role`` at ``school``.

        A teacher's code must be an unclaimed invite for that school; a student
        or parent's must equal the school's shared join code. A mismatched
        school/code pair fails here, never a silent misassignment.
        """
        if role == Role.TEACHER:
            invite = TeacherInvite.objects.filter(
                school=school, code=code, claimed_by__isnull=True
            ).first()
            if invite is None:
                self.add_error(
                    "code",
                    "That teacher code is not valid for the selected school, "
                    "or it has already been used.",
                )
        elif role == Role.STUDENT:
            if code != (school.student_join_code or "").upper():
                self.add_error(
                    "code",
                    "That is not the correct student join code for this school.",
                )
        elif role == Role.PARENT:
            if code != (school.parent_student_join_code or "").upper():
                self.add_error(
                    "code",
                    "That is not the correct parent join code for this school.",
                )

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
        user.school = self.cleaned_data.get("school")

        if not commit:
            return user

        # The account, the (single-use) teacher-code claim, and the optional
        # parent link are one unit. If the claim loses a race, the whole thing
        # rolls back — no account is left with a broken or double-used code.
        with transaction.atomic():
            user.save()

            if user.role == Role.TEACHER:
                claimed = TeacherInvite.claim(
                    school=user.school,
                    code=self.cleaned_data["code"],
                    user=user,
                )
                if not claimed:
                    raise RegistrationError(
                        "That teacher code was just used by someone else."
                    )

            child = self.cleaned_data.get("child_username")
            if child is not None:
                ParentStudentLink.objects.get_or_create(parent=user, student=child)

        return user


class SchoolRegistrationForm(UserCreationForm):
    """Register a school admin and their school in one step.

    Creates a ``User(role="school_admin")`` and the ``School`` they administer,
    and points the admin's own ``User.school`` at it — so every role, this one
    included, is reachable through ``request.user.school``.
    """

    school_name = forms.CharField(
        max_length=200,
        label="School name",
        help_text="The name learners and teachers will see when they register.",
    )
    preset = forms.ChoiceField(
        choices=PRESET_CHOICES,
        initial="primary",
        label="Grade range",
        help_text="Pick a preset, or choose Custom to set an exact range.",
    )
    min_grade = forms.ChoiceField(
        choices=GRADE_CHOICES, required=False, label="From grade (custom only)"
    )
    max_grade = forms.ChoiceField(
        choices=GRADE_CHOICES, required=False, label="To grade (custom only)"
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name", "email")

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()

    def clean_school_name(self):
        name = self.cleaned_data["school_name"].strip()
        if School.objects.filter(name__iexact=name).exists():
            raise forms.ValidationError(
                "A school with that name is already registered."
            )
        return name

    def clean(self):
        cleaned = super().clean()
        preset = cleaned.get("preset")

        if preset in GRADE_PRESETS:
            cleaned["min_grade"], cleaned["max_grade"] = GRADE_PRESETS[preset]
            return cleaned

        # Custom range: both bounds required, ordered, and within 1-12.
        low = cleaned.get("min_grade")
        high = cleaned.get("max_grade")
        if not low or not high:
            self.add_error(
                "min_grade", "Set both grades for a custom range, or pick a preset."
            )
            return cleaned

        low, high = int(low), int(high)
        if low > high:
            self.add_error("min_grade", "The lower grade cannot exceed the upper one.")
        if not (SCHOOL_MIN_GRADE <= low <= SCHOOL_MAX_GRADE) or not (
            SCHOOL_MIN_GRADE <= high <= SCHOOL_MAX_GRADE
        ):
            self.add_error("max_grade", "Grades must be between 1 and 12.")

        cleaned["min_grade"], cleaned["max_grade"] = low, high
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = Role.SCHOOL_ADMIN

        if not commit:
            return user

        # The user, the school, and the back-link are one unit: if any part
        # fails, none of it should persist as a half-registered admin.
        with transaction.atomic():
            user.save()
            school = School.objects.create(
                name=self.cleaned_data["school_name"],
                min_grade=self.cleaned_data["min_grade"],
                max_grade=self.cleaned_data["max_grade"],
                created_by=user,
            )
            user.school = school
            user.save(update_fields=["school"])

        return user


class TeacherInviteForm(forms.ModelForm):
    """A school admin lists a teacher by name and assigned grade(s).

    Produces a :class:`TeacherInvite` with a generated unique code; the school
    is taken from the logged-in admin, never the request body.
    """

    class Meta:
        model = TeacherInvite
        fields = ("teacher_name", "assigned_grades")
        labels = {
            "teacher_name": "Teacher's name",
            "assigned_grades": "Assigned grade(s)",
        }

    def __init__(self, *args, school=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.school = school

    def save(self, commit=True):
        # commit is honoured implicitly: create_for always writes, which is
        # what the caller wants (there is no draft invite).
        return TeacherInvite.create_for(
            school=self.school,
            teacher_name=self.cleaned_data["teacher_name"],
            assigned_grades=self.cleaned_data["assigned_grades"],
        )
