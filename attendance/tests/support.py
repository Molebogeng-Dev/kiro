"""Shared helpers for the attendance tests.

Face descriptors here are plain 128-length vectors, not real face-api.js output,
which is all the server-side matching needs: two identical vectors are distance
0 (a match), and vectors far apart exceed the threshold (no match). No camera,
no models, no network.
"""

from accounts.models import Role, User

PASSWORD = "attendance-tests-passphrase-55"

DESCRIPTOR_LENGTH = 128


def make_teacher(username="teacher"):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=PASSWORD,
        role=Role.TEACHER,
    )


def make_student(username, grade):
    # No first_name on purpose: the templates fall back to the username, which
    # is what the tests assert on, keeping those assertions stable.
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=PASSWORD,
        role=Role.STUDENT,
        grade=grade,
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
