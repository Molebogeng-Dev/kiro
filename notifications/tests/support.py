"""Shared helpers for the notification tests.

Nothing here touches the network. The summary's OpenRouter call and the Twilio
send are mocked in the individual tests; these helpers only build the database
rows those code paths read: a marked paper, its result, and a parent linked to
its student.
"""

from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile

from accounts.models import ParentStudentLink, Role, User
from core.models import School
from marking.models import MarkingResult, Memorandum, Paper, QuestionResult

PASSWORD = "notifications-tests-passphrase-91"


def default_school():
    """A shared school for these helpers (used by notification and progress
    tests). Sprint 8b scopes the progress dashboard by school, so a teacher and
    the learners they see must share one unless a test overrides it."""
    school, _ = School.objects.get_or_create(
        name="Notifications Test School", defaults={"min_grade": 1, "max_grade": 12}
    )
    return school

# A tiny valid file. The marking image content is irrelevant to these tests —
# nothing re-reads or re-marks the image here — so a stub avoids Pillow work.
_STUB_IMAGE = SimpleUploadedFile("paper.jpg", b"not-a-real-jpeg", "image/jpeg")


def make_teacher(username="teacher", **extra):
    extra.setdefault("school", default_school())
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=PASSWORD,
        role=Role.TEACHER,
        **extra,
    )


def make_student(username="student", **extra):
    extra.setdefault("school", default_school())
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=PASSWORD,
        role=Role.STUDENT,
        grade=extra.pop("grade", 9),
        **extra,
    )


def make_parent(username="parent", phone_number="+27821234567", **extra):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=PASSWORD,
        role=Role.PARENT,
        phone_number=phone_number,
        **extra,
    )


def link(parent, student):
    return ParentStudentLink.objects.create(parent=parent, student=student)


def make_memorandum(**overrides):
    defaults = {
        "title": "Grade 9 Mathematics Test 2",
        "subject": "Mathematics",
        "content": "Question 1 (10 marks)\nExpected: show all working.",
        "total_marks": 10,
    }
    return Memorandum.objects.create(**{**defaults, **overrides})


def make_marked_paper(student, *, teacher=None, memorandum=None, with_result=True):
    """A paper in the ``marked`` state, optionally with a full result attached."""
    teacher = teacher or make_teacher(f"teacher-for-{student.username}")
    memorandum = memorandum or make_memorandum()

    paper = Paper.objects.create(
        memorandum=memorandum,
        submitted_by=teacher,
        student=student,
        image=SimpleUploadedFile("p.jpg", b"stub", "image/jpeg"),
        status=Paper.Status.MARKED,
    )

    if with_result:
        attach_result(paper)

    return paper


def attach_result(paper):
    """Give ``paper`` a MarkingResult with two questions, like the engine would."""
    result = MarkingResult.objects.create(
        paper=paper,
        marks_awarded=Decimal("7.00"),
        marks_available=Decimal("10.00"),
        summary="Good grasp of the method, a couple of arithmetic slips.",
        model_used="qwen/qwen2.5-vl-72b-instruct",
        raw_response="{}",
    )
    QuestionResult.objects.bulk_create(
        [
            QuestionResult(
                result=result,
                number="1",
                marks_awarded=Decimal("4.00"),
                marks_available=Decimal("5.00"),
                feedback="Method correct, small slip in the final step.",
                position=1,
            ),
            QuestionResult(
                result=result,
                number="2",
                marks_awarded=Decimal("3.00"),
                marks_available=Decimal("5.00"),
                feedback="Remember to show the units.",
                position=2,
            ),
        ]
    )
    return result
