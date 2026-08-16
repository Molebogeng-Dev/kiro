"""Every attendance view is teacher-only, consistent with earlier sprints."""

from django.test import TestCase
from django.urls import reverse

from .support import make_parent, make_student, make_teacher

# (url name, http method). GET for pages, POST for the write-only fallback.
GET_VIEWS = [
    "attendance:index",
    "attendance:roll_call",
    "attendance:enroll",
    "attendance:check_in",
    "attendance:history",
]


class AttendanceAccessControlTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = make_teacher("acc-teacher")
        cls.student = make_student("acc-student", grade=9)
        cls.parent = make_parent("acc-parent")

    def test_a_teacher_reaches_every_page(self):
        self.client.force_login(self.teacher)
        for name in GET_VIEWS:
            with self.subTest(url=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_a_student_is_refused_every_page(self):
        self.client.force_login(self.student)
        for name in GET_VIEWS:
            with self.subTest(url=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_a_parent_is_refused_every_page(self):
        self.client.force_login(self.parent)
        for name in GET_VIEWS:
            with self.subTest(url=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_an_anonymous_visitor_is_sent_to_login(self):
        for name in GET_VIEWS:
            with self.subTest(url=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("accounts:login"), response.headers["Location"])

    def test_a_student_cannot_reach_the_manual_fallback_endpoint(self):
        self.client.force_login(self.student)
        response = self.client.post(
            reverse("attendance:mark_present"), {"student": self.student.id}
        )
        self.assertEqual(response.status_code, 403)

    def test_a_student_cannot_run_the_roll_call(self):
        self.client.force_login(self.student)
        response = self.client.post(
            reverse("attendance:roll_call"), {"present": [self.student.id]}
        )
        self.assertEqual(response.status_code, 403)
