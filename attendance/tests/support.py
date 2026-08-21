"""Shared helpers for the attendance tests.

Face descriptors here are plain 128-length vectors, not real face-api.js output,
which is all the server-side matching needs: two identical vectors are distance
0 (a match), and vectors far apart exceed the threshold (no match). No camera,
no models, no network.
"""

from accounts.models import Role, User
from core.models import School

PASSWORD = "attendance-tests-passphrase-55"

DESCRIPTOR_LENGTH = 128


def default_school():
    """One school, covering all grades, shared by these helpers by default.

    Sprint 8b scopes every attendance view to the teacher's school, so a teacher
    and the learners they roll-call must share one; cross-school tests pass an
    explicit ``school``.
    """
    school, _ = School.objects.get_or_create(
        name="Attendance Test School", defaults={"min_grade": 1, "max_grade": 12}
    )
    return school


def make_teacher(username="teacher", school=None):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=PASSWORD,
        role=Role.TEACHER,
        school=school or default_school(),
    )


def make_student(username, grade, school=None):
    # No first_name on purpose: the templates fall back to the username, which
    # is what the tests assert on, keeping those assertions stable.
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=PASSWORD,
        role=Role.STUDENT,
        grade=grade,
        school=school or default_school(),
    )


def make_parent(username="parent"):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=PASSWORD,
        role=Role.PARENT,
    )


def descriptor(value=0.0):
    """A flat 128-length descriptor. Distance between two of these is
    |a-b| * sqrt(128) ≈ |a-b| * 11.31, so 0.0 vs 0.0 matches and 0.0 vs 0.5
    (distance ≈ 5.7) is well past the 0.6 threshold."""
    return [float(value)] * DESCRIPTOR_LENGTH
