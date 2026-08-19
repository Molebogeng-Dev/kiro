"""The parent portal: seeing a linked child's work and attendance, and only theirs.

Two concerns run through these tests. First, role: the parent pages are
parent-only, refusing teachers and students exactly as every other portal in the
app does. Second, and the one this sprint turns on, the ownership boundary — a
parent can only ever reach a child they are linked to via ``ParentStudentLink``,
and an unlinked child (or another family's paper) is filtered out *before* the
lookup, so it is a 404, never a page that confirms the record exists.

These live in their own module (not the Sprint 2 ``core/tests.py``) so the
sprint-to-module mapping in run.sh keeps them under Sprint 6.
"""

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from attendance.models import Attendance
from marking.models import Paper

# The parent portal helpers live with the notification tests; this is the same
# sprint, so reusing them keeps one source of truth for building linked families.
from notifications.tests.support import (
    attach_result,
    link,
    make_marked_paper,
    make_memorandum,
    make_parent,
    make_student,
    make_teacher,
)


class ParentPortalAccessTests(TestCase):
    """Role enforcement, consistent with every other portal."""

    @classmethod
    def setUpTestData(cls):
        cls.parent = make_parent("access-parent")
        cls.child = make_student("access-child")
        link(cls.parent, cls.child)
        cls.teacher = make_teacher("access-teacher")
        cls.other_student = make_student("access-other-student")

    def test_a_parent_reaches_their_own_child_page(self):
        self.client.force_login(self.parent)
        response = self.client.get(
            reverse("core:parent_child", args=[self.child.id])
        )
        self.assertEqual(response.status_code, 200)

    def test_a_teacher_is_refused_the_parent_pages(self):
        self.client.force_login(self.teacher)
        for url in self._parent_urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_a_student_is_refused_the_parent_pages(self):
        self.client.force_login(self.other_student)
        for url in self._parent_urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_an_anonymous_visitor_is_sent_to_login(self):
        response = self.client.get(reverse("core:parent_dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.headers["Location"])

    def _parent_urls(self):
        return [
            reverse("core:parent_dashboard"),
            reverse("core:parent_child", args=[self.child.id]),
        ]


class ParentDashboardTests(TestCase):
    def test_a_parent_with_several_children_sees_the_picker(self):
        parent = make_parent("multi-parent")
        alice = make_student("alice", first_name="Alice")
        bongani = make_student("bongani", first_name="Bongani")
        link(parent, alice)
        link(parent, bongani)

        self.client.force_login(parent)
        response = self.client.get(reverse("core:parent_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alice")
        self.assertContains(response, "Bongani")

    def test_a_parent_with_one_child_skips_straight_to_them(self):
        parent = make_parent("solo-parent")
        child = make_student("solo-child")
        link(parent, child)

        self.client.force_login(parent)
        response = self.client.get(reverse("core:parent_dashboard"))

        self.assertRedirects(
            response,
            reverse("core:parent_child", args=[child.id]),
            fetch_redirect_response=False,
        )

    def test_a_parent_with_no_children_sees_an_empty_state(self):
        parent = make_parent("childless-parent")
        self.client.force_login(parent)
        response = self.client.get(reverse("core:parent_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No child linked yet")


class ParentChildViewTests(TestCase):
    def setUp(self):
        self.parent = make_parent("child-view-parent")
        self.child = make_student("child-view-child", first_name="Naledi")
        link(self.parent, self.child)
        self.client.force_login(self.parent)

    def test_a_childs_marked_work_is_listed(self):
        make_marked_paper(self.child, memorandum=make_memorandum(title="Algebra Test"))

        response = self.client.get(
            reverse("core:parent_child", args=[self.child.id])
        )
        self.assertContains(response, "Algebra Test")

    def test_a_childs_attendance_is_listed(self):
        Attendance.objects.create(
            student=self.child,
            date=timezone.localdate(),
            method=Attendance.Method.FACIAL,
            arrived_at=timezone.now(),
        )
        response = self.client.get(
            reverse("core:parent_child", args=[self.child.id])
        )
        self.assertContains(response, "Facial")

    def test_a_multi_child_parent_gets_a_switcher(self):
        sibling = make_student("child-view-sibling", first_name="Kabelo")
        link(self.parent, sibling)

        response = self.client.get(
            reverse("core:parent_child", args=[self.child.id])
        )
        # The other child is offered as a switch target.
        self.assertContains(response, "Kabelo")
        self.assertContains(
            response, reverse("core:parent_child", args=[sibling.id])
        )


class ParentOwnershipBoundaryTests(TestCase):
    """The heart of the sprint: a parent cannot reach another family's data."""

    def setUp(self):
        self.parent = make_parent("boundary-parent")
        self.own_child = make_student("own-child")
        link(self.parent, self.own_child)

        # Another family, entirely unrelated to this parent.
        self.other_child = make_student("other-child")
        self.client.force_login(self.parent)

    def test_an_unlinked_child_is_a_404_not_a_forbidden(self):
        # Filtered out before the lookup, so it is indistinguishable from a
        # child that does not exist — never a page that confirms it does.
        response = self.client.get(
            reverse("core:parent_child", args=[self.other_child.id])
        )
        self.assertEqual(response.status_code, 404)

    def test_another_familys_paper_is_a_404(self):
        other_paper = make_marked_paper(self.other_child)
        response = self.client.get(
            reverse("core:parent_paper", args=[other_paper.id])
        )
        self.assertEqual(response.status_code, 404)

    def test_an_unmarked_paper_of_ones_own_child_is_a_404(self):
        pending = Paper.objects.create(
            memorandum=make_memorandum(),
            submitted_by=make_teacher("pending-teacher"),
            student=self.own_child,
            image=_stub_image(),
            status=Paper.Status.PENDING,
        )
        response = self.client.get(
            reverse("core:parent_paper", args=[pending.id])
        )
        self.assertEqual(response.status_code, 404)

    def test_a_parent_can_open_their_own_childs_marked_paper(self):
        paper = make_marked_paper(self.own_child)
        response = self.client.get(reverse("core:parent_paper", args=[paper.id]))

        self.assertEqual(response.status_code, 200)
        # Reuses the shared result display: the per-question feedback is shown.
        self.assertContains(response, "show the units")


def _stub_image():
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile("p.jpg", b"stub", "image/jpeg")
