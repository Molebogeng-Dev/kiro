"""School-scoping helpers (Sprint 8b).

Every "any teacher, any student" query became "any teacher, any student *at the
same school*" this sprint. The one subtlety worth centralising is ``None``:
accounts created before Sprint 8a have ``school=None``, and a bare
``filter(school=user.school)`` with ``user.school`` being ``None`` would match
*every* school-less row — a teacher with no school would suddenly "share" a
school with every other unassigned account.

So the rule lives in one place: **a ``None`` school scopes to nothing.** A user
with no school sees no one, rather than everyone else who also has no school.
Every scoped query in the app goes through here.
"""

from .models import Role, User


def scope_by_school(queryset, school, *, field="school"):
    """Filter ``queryset`` to ``school`` on ``field``, or to nothing if None.

    ``field`` lets a related model reach the school across a relation, e.g.
    ``field="student__school"`` for attendance rows or ``"created_by__school"``
    for teacher-owned content.
    """
    if school is None:
        return queryset.none()
    return queryset.filter(**{field: school})


def students_at_school(school, *, queryset=None):
    """Student accounts at ``school`` (empty when ``school`` is None).

    Pass ``queryset`` to narrow an already-filtered set (e.g. a grade band);
    it defaults to all students.
    """
    base = queryset if queryset is not None else User.objects.filter(role=Role.STUDENT)
    return scope_by_school(base, school)
