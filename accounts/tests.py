"""Tests for roles, registration, and role-based access control.

The access-control tests are the important ones: they are what stops a later
sprint from quietly widening access while adding a feature.
"""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.utils import IntegrityError
from django.test import TestCase
from django.urls import reverse

from core.models import School, TeacherInvite

from .models import ParentStudentLink, Role, User

DASHBOARD_PATHS = {
    Role.TEACHER: "/teacher/",
    Role.STUDENT: "/student/",
    Role.PARENT: "/parent/",
}

PASSWORD = "an-oddly-specific-passphrase-42"


def make_user(username, role=None, **extra):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=PASSWORD,
        role=role or "",
        **extra,
    )


class RoleAccessControlTests(TestCase):
    """A role may reach its own dashboard and nothing else."""

    @classmethod
    def setUpTestData(cls):
        cls.users = {role: make_user(role.value, role) for role in Role}

    def test_each_role_can_reach_its_own_dashboard(self):
        for role, path in DASHBOARD_PATHS.items():
            with self.subTest(role=role):
                self.client.force_login(self.users[role])
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)

    def test_role_cannot_reach_another_roles_dashboard_by_url(self):
        for role in Role:
            self.client.force_login(self.users[role])
            for other_role, path in DASHBOARD_PATHS.items():
                if other_role == role:
                    continue
                with self.subTest(logged_in_as=role, requesting=other_role):
                    response = self.client.get(path)
                    self.assertEqual(
                        response.status_code,
                        403,
                        f"a {role.value} should not be able to load {path}",
                    )

    def test_anonymous_visitor_is_sent_to_login(self):
        for path in DASHBOARD_PATHS.values():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertRedirects(
                    response, f"{reverse('accounts:login')}?next={path}"
                )

    def test_account_without_a_role_is_refused(self):
        """A role is required; an account missing one gets no dashboard."""
        self.client.force_login(make_user("roleless"))

        self.assertEqual(self.client.get(reverse("core:home")).status_code, 403)
        for path in DASHBOARD_PATHS.values():
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 403)


class HomeRoutingTests(TestCase):
    """The single post-login target forwards each role to the right place."""

    def test_home_redirects_to_the_dashboard_for_the_role(self):
        for role, path in DASHBOARD_PATHS.items():
            with self.subTest(role=role):
                self.client.force_login(make_user(f"router-{role.value}", role))
                response = self.client.get(reverse("core:home"))
                self.assertRedirects(response, path)


class RegistrationTests(TestCase):
    """Registration works for all three roles and lands on the right dashboard."""

    def setUp(self):
        # Every self-registration now joins a school with a code (Sprint 8a).
        self.school = School.objects.create(
            name="Registration Test School", min_grade=1, max_grade=12
        )

    def _code_for(self, role_value):
        """The right registration code for a role at ``self.school``."""
        if role_value == Role.TEACHER.value:
            return TeacherInvite.create_for(
                school=self.school, teacher_name="Listed Teacher", assigned_grades="8"
            ).code
        if role_value == Role.PARENT.value:
            return self.school.parent_student_join_code
        # Students and any role the form should reject fall back to the student
        # code; a rejected role errors on the role field regardless of the code.
        return self.school.student_join_code

    def register(self, username, role, follow=False, **extra):
        """POST the registration form. ``role`` may be a Role or a raw string,
        so a test can send something the form should reject."""
        role_value = getattr(role, "value", role)
        payload = {
            "username": username,
            "first_name": "Test",
            "last_name": "Person",
            "email": f"{username}@example.com",
            "role": role_value,
            # Required for students since Sprint 5; harmless for other roles,
            # where the form discards it.
            "grade": "8",
            # Required for parents since Sprint 6; discarded for other roles.
            "phone_number": "+27821234567",
            # School + code required since Sprint 8a.
            "school": self.school.id,
            "code": self._code_for(role_value),
            "password1": PASSWORD,
            "password2": PASSWORD,
        }
        payload.update(extra)
        return self.client.post(reverse("accounts:register"), payload, follow=follow)

    def test_can_register_as_each_role_and_land_on_own_dashboard(self):
        for role, path in DASHBOARD_PATHS.items():
            with self.subTest(role=role):
                self.client.logout()
                response = self.register(f"new-{role.value}", role, follow=True)

                # Registration logs the new user in and hands off to core:home,
                # which forwards to the dashboard for that account's role.
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.redirect_chain[-1][0], path)

                user = User.objects.get(username=f"new-{role.value}")
                self.assertEqual(user.role, role)

    def test_login_sends_each_role_to_its_own_dashboard(self):
        for role, path in DASHBOARD_PATHS.items():
            with self.subTest(role=role):
                user = make_user(f"login-{role.value}", role)
                self.client.logout()
                response = self.client.post(
                    reverse("accounts:login"),
                    {"username": user.username, "password": PASSWORD},
                    follow=True,
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.redirect_chain[-1][0], path)

    def test_role_is_required(self):
        response = self.register("no-role", "")
        self.assertEqual(response.status_code, 200)
        self.assertIn("role", response.context["form"].errors)
        self.assertFalse(User.objects.filter(username="no-role").exists())

    def test_an_invalid_role_is_rejected(self):
        """Roles come from a fixed set; a hand-crafted POST cannot invent one."""
        response = self.register("principal", "principal")
        self.assertEqual(response.status_code, 200)
        self.assertIn("role", response.context["form"].errors)
        self.assertFalse(User.objects.filter(username="principal").exists())

    def test_email_must_be_unique(self):
        make_user("first", Role.STUDENT)
        response = self.client.post(
            reverse("accounts:register"),
            {
                "username": "second",
                "email": "first@example.com",
                "role": Role.PARENT.value,
                "password1": PASSWORD,
                "password2": PASSWORD,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("email", response.context["form"].errors)


class ParentStudentLinkTests(TestCase):
    """The link later sprints use to route results to the right parent."""

    @classmethod
    def setUpTestData(cls):
        cls.school = School.objects.create(name="Link Test School")
        cls.student = make_user("learner", Role.STUDENT)
        cls.mother = make_user("mother", Role.PARENT)
        cls.father = make_user("father", Role.PARENT)

    def test_a_student_can_have_several_parents(self):
        ParentStudentLink.objects.create(parent=self.mother, student=self.student)
        ParentStudentLink.objects.create(parent=self.father, student=self.student)

        self.assertEqual(self.student.parents.count(), 2)
        self.assertEqual(list(self.mother.children), [self.student])

    def test_the_same_pair_cannot_be_linked_twice(self):
        ParentStudentLink.objects.create(parent=self.mother, student=self.student)

        # Wrapped in atomic() so the failed insert does not poison the
        # surrounding test transaction.
        with self.assertRaises(IntegrityError), transaction.atomic():
            ParentStudentLink.objects.create(parent=self.mother, student=self.student)

    def test_roles_on_each_side_of_the_link_are_validated(self):
        teacher = make_user("teacher-not-parent", Role.TEACHER)

        with self.assertRaises(ValidationError):
            ParentStudentLink(parent=teacher, student=self.student).full_clean()

        with self.assertRaises(ValidationError):
            ParentStudentLink(parent=self.mother, student=self.father).full_clean()

    def test_parent_can_link_their_child_while_registering(self):
        response = self.client.post(
            reverse("accounts:register"),
            {
                "username": "guardian",
                "email": "guardian@example.com",
                "role": Role.PARENT.value,
                "phone_number": "+27821234567",
                "school": self.school.id,
                "code": self.school.parent_student_join_code,
                "password1": PASSWORD,
                "password2": PASSWORD,
                "child_username": "learner",
            },
            follow=True,
        )
        # A parent with exactly one linked child lands straight on that child's
        # page rather than a picker of one (Sprint 6 behaviour).
        self.assertEqual(
            response.redirect_chain[-1][0], f"/parent/child/{self.student.id}/"
        )

        guardian = User.objects.get(username="guardian")
        self.assertEqual(list(guardian.children), [self.student])

    def test_linking_an_unknown_child_is_rejected(self):
        response = self.client.post(
            reverse("accounts:register"),
            {
                "username": "guardian2",
                "email": "guardian2@example.com",
                "role": Role.PARENT.value,
                "password1": PASSWORD,
                "password2": PASSWORD,
                "child_username": "nobody",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("child_username", response.context["form"].errors)
        self.assertFalse(User.objects.filter(username="guardian2").exists())

    def test_a_non_parent_cannot_link_a_child(self):
        response = self.client.post(
            reverse("accounts:register"),
            {
                "username": "sneaky-teacher",
                "email": "sneaky@example.com",
                "role": Role.TEACHER.value,
                "password1": PASSWORD,
                "password2": PASSWORD,
                "child_username": "learner",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("child_username", response.context["form"].errors)
        self.assertFalse(User.objects.filter(username="sneaky-teacher").exists())
