"""Schools, school admins, and code-based registration (Sprint 8a).

The through-line: after this sprint every account carries a ``school``, set at
registration and gated by a code. A school admin creates a school and lists its
teachers; a teacher claims a single-use invite; a student or parent types a
shared, reusable join code. The correctness that matters most is the teacher
code claiming exactly once, so two people cannot both register into the same
slot.

These live in their own module (not the Sprint 1 ``accounts`` package) so the
run.sh sprint mapping keeps them under Sprint 8.
"""

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from core.models import School, TeacherInvite

PASSWORD = "an-oddly-specific-passphrase-42"


def make_school(name="Test School", min_grade=1, max_grade=12, **extra):
    return School.objects.create(
        name=name, min_grade=min_grade, max_grade=max_grade, **extra
    )


def make_school_admin(username="admin", school=None):
    admin = User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=PASSWORD,
        role=Role.SCHOOL_ADMIN,
    )
    if school is not None:
        admin.school = school
        admin.save(update_fields=["school"])
    return admin


def make_user(username, role):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=PASSWORD,
        role=role,
    )


# --------------------------------------------------------------------------- #
# School admin registration
# --------------------------------------------------------------------------- #


class SchoolRegistrationTests(TestCase):
    def _register_school(self, **overrides):
        payload = {
            "username": "principal",
            "first_name": "Head",
            "last_name": "Teacher",
            "email": "principal@example.com",
            "school_name": "Rivertown Primary",
            "preset": "primary",
            "password1": PASSWORD,
            "password2": PASSWORD,
        }
        payload.update(overrides)
        return self.client.post(reverse("accounts:register_school"), payload, follow=True)

    def test_a_primary_preset_creates_the_school_and_links_the_admin(self):
        response = self._register_school()

        admin = User.objects.get(username="principal")
        school = School.objects.get(name="Rivertown Primary")

        self.assertEqual(admin.role, Role.SCHOOL_ADMIN)
        # The admin's own school points at the one they just created — every
        # role, this one included, is reachable via request.user.school.
        self.assertEqual(admin.school, school)
        self.assertEqual(school.created_by, admin)
        self.assertEqual((school.min_grade, school.max_grade), (1, 7))
        # And they land on their own dashboard.
        self.assertEqual(response.redirect_chain[-1][0], "/school/")

    def test_a_secondary_preset_stores_eight_to_twelve(self):
        self._register_school(
            username="sec-admin",
            email="sec@example.com",
            school_name="Hilltop Secondary",
            preset="secondary",
        )
        school = School.objects.get(name="Hilltop Secondary")
        self.assertEqual((school.min_grade, school.max_grade), (8, 12))

    def test_a_custom_range_is_stored(self):
        self._register_school(
            username="cust-admin",
            email="cust@example.com",
            school_name="Combined School",
            preset="custom",
            min_grade="4",
            max_grade="9",
        )
        school = School.objects.get(name="Combined School")
        self.assertEqual((school.min_grade, school.max_grade), (4, 9))

    def test_a_custom_range_needs_both_bounds(self):
        response = self._register_school(
            school_name="Broken School", preset="custom", min_grade="4"
        )
        self.assertFalse(School.objects.filter(name="Broken School").exists())
        self.assertFalse(User.objects.filter(username="principal").exists())
        self.assertTrue(response.context["form"].errors)

    def test_a_reversed_custom_range_is_rejected(self):
        self._register_school(
            school_name="Reversed School",
            preset="custom",
            min_grade="9",
            max_grade="4",
        )
        self.assertFalse(School.objects.filter(name="Reversed School").exists())

    def test_a_school_registers_with_two_join_codes(self):
        self._register_school()
        school = School.objects.get(name="Rivertown Primary")
        self.assertTrue(school.student_join_code)
        self.assertTrue(school.parent_student_join_code)

    def test_a_duplicate_school_name_is_rejected(self):
        make_school(name="Rivertown Primary")
        response = self._register_school()
        self.assertIn("school_name", response.context["form"].errors)
        self.assertFalse(User.objects.filter(username="principal").exists())


# --------------------------------------------------------------------------- #
# Access control
# --------------------------------------------------------------------------- #


class SchoolAdminAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.school = make_school()
        cls.admin = make_school_admin(school=cls.school)
        cls.teacher = make_user("a-teacher", Role.TEACHER)
        cls.student = make_user("a-student", Role.STUDENT)

    def test_a_school_admin_reaches_their_dashboard(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get("/school/").status_code, 200)

    def test_home_routes_a_school_admin_to_their_dashboard(self):
        self.client.force_login(self.admin)
        self.assertRedirects(self.client.get(reverse("core:home")), "/school/")

    def test_non_admins_are_refused_the_school_dashboard(self):
        for user in (self.teacher, self.student):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                self.assertEqual(self.client.get("/school/").status_code, 403)

    def test_a_school_admin_cannot_reach_a_teacher_dashboard(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get("/teacher/").status_code, 403)

    def test_an_anonymous_visitor_is_sent_to_login(self):
        response = self.client.get("/school/")
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.headers["Location"])


# --------------------------------------------------------------------------- #
# Teacher invites: creation and single-use claiming
# --------------------------------------------------------------------------- #


class TeacherInviteTests(TestCase):
    def setUp(self):
        self.school = make_school()
        self.admin = make_school_admin(school=self.school)

    def test_an_admin_lists_a_teacher_and_gets_a_code(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("core:school_admin_dashboard"),
            {"teacher_name": "Mr Dlamini", "assigned_grades": "Grade 8"},
            follow=True,
        )

        invite = TeacherInvite.objects.get(school=self.school)
        self.assertEqual(invite.teacher_name, "Mr Dlamini")
        self.assertTrue(invite.code)
        self.assertFalse(invite.is_claimed)
        # The code is shown back to the admin to share.
        self.assertContains(response, invite.code)

    def test_generated_codes_are_unique(self):
        codes = {
            TeacherInvite.create_for(
                school=self.school, teacher_name=f"T{i}", assigned_grades="8"
            ).code
            for i in range(25)
        }
        self.assertEqual(len(codes), 25)

    def test_a_code_can_be_claimed_only_once(self):
        invite = TeacherInvite.create_for(
            school=self.school, teacher_name="T", assigned_grades="8"
        )
        first = make_user("claimer-1", Role.TEACHER)
        second = make_user("claimer-2", Role.TEACHER)

        # The atomic conditional UPDATE is the race guard: the first claim wins,
        # the second sees no still-open row and fails — never a silent reassign.
        self.assertTrue(
            TeacherInvite.claim(school=self.school, code=invite.code, user=first)
        )
        self.assertFalse(
            TeacherInvite.claim(school=self.school, code=invite.code, user=second)
        )

        invite.refresh_from_db()
        self.assertEqual(invite.claimed_by, first)
        self.assertIsNotNone(invite.claimed_at)

    def test_a_code_only_claims_at_its_own_school(self):
        invite = TeacherInvite.create_for(
            school=self.school, teacher_name="T", assigned_grades="8"
        )
        other_school = make_school(name="Other School")
        user = make_user("wrong-school-teacher", Role.TEACHER)

        self.assertFalse(
            TeacherInvite.claim(school=other_school, code=invite.code, user=user)
        )
        invite.refresh_from_db()
        self.assertFalse(invite.is_claimed)


# --------------------------------------------------------------------------- #
# Registration against codes
# --------------------------------------------------------------------------- #


class RegistrationHelper:
    """Shared POST helper for the main (teacher/student/parent) form."""

    def register(self, **fields):
        # Registration is for anonymous visitors; a successful one logs the new
        # account in, so start each call logged out (matters when a test
        # registers more than once).
        self.client.logout()
        payload = {
            "first_name": "Test",
            "last_name": "Person",
            "password1": PASSWORD,
            "password2": PASSWORD,
        }
        payload.update(fields)
        return self.client.post(reverse("accounts:register"), payload)


class TeacherRegistrationTests(RegistrationHelper, TestCase):
    def setUp(self):
        self.school = make_school()

    def _invite(self, teacher_name="Ms Naidoo"):
        return TeacherInvite.create_for(
            school=self.school, teacher_name=teacher_name, assigned_grades="8"
        )

    def test_a_valid_code_creates_the_teacher_and_claims_the_invite(self):
        invite = self._invite()
        self.register(
            username="teacher-ok",
            email="teacher-ok@example.com",
            role=Role.TEACHER.value,
            school=self.school.id,
            code=invite.code,
        )

        teacher = User.objects.get(username="teacher-ok")
        self.assertEqual(teacher.school, self.school)

        invite.refresh_from_db()
        self.assertEqual(invite.claimed_by, teacher)
        self.assertIsNotNone(invite.claimed_at)

    def test_an_invalid_code_creates_no_account(self):
        response = self.register(
            username="teacher-bad",
            email="teacher-bad@example.com",
            role=Role.TEACHER.value,
            school=self.school.id,
            code="NOTACODE",
        )
        self.assertIn("code", response.context["form"].errors)
        self.assertFalse(User.objects.filter(username="teacher-bad").exists())

    def test_an_already_claimed_code_creates_no_second_account(self):
        invite = self._invite()
        self.register(
            username="teacher-first",
            email="first@example.com",
            role=Role.TEACHER.value,
            school=self.school.id,
            code=invite.code,
        )

        response = self.register(
            username="teacher-second",
            email="second@example.com",
            role=Role.TEACHER.value,
            school=self.school.id,
            code=invite.code,
        )

        self.assertIn("code", response.context["form"].errors)
        self.assertFalse(User.objects.filter(username="teacher-second").exists())

    def test_a_code_for_another_school_is_rejected(self):
        invite = self._invite()
        other_school = make_school(name="Different School")

        response = self.register(
            username="teacher-mismatch",
            email="mismatch@example.com",
            role=Role.TEACHER.value,
            school=other_school.id,  # selected the wrong school for this code
            code=invite.code,
        )
        self.assertIn("code", response.context["form"].errors)
        self.assertFalse(User.objects.filter(username="teacher-mismatch").exists())


class StudentParentRegistrationTests(RegistrationHelper, TestCase):
    def setUp(self):
        self.school = make_school()

    def test_a_student_registers_with_the_shared_code(self):
        self.register(
            username="pupil",
            email="pupil@example.com",
            role=Role.STUDENT.value,
            grade="6",
            school=self.school.id,
            code=self.school.student_join_code,
        )
        pupil = User.objects.get(username="pupil")
        self.assertEqual(pupil.school, self.school)
        self.assertEqual(pupil.grade, 6)

    def test_the_student_code_is_reusable_by_many_students(self):
        for i in range(3):
            self.register(
                username=f"pupil-{i}",
                email=f"pupil-{i}@example.com",
                role=Role.STUDENT.value,
                grade="6",
                school=self.school.id,
                code=self.school.student_join_code,
            )
        # The same shared code is not consumed by use.
        self.assertEqual(
            User.objects.filter(username__startswith="pupil-").count(), 3
        )

    def test_a_wrong_student_code_creates_no_account(self):
        response = self.register(
            username="pupil-bad",
            email="pupil-bad@example.com",
            role=Role.STUDENT.value,
            grade="6",
            school=self.school.id,
            code="WRONGCODE",
        )
        self.assertIn("code", response.context["form"].errors)
        self.assertFalse(User.objects.filter(username="pupil-bad").exists())

    def test_a_parent_registers_with_the_parent_code(self):
        self.register(
            username="guardian",
            email="guardian@example.com",
            role=Role.PARENT.value,
            phone_number="+27821234567",
            school=self.school.id,
            code=self.school.parent_student_join_code,
        )
        guardian = User.objects.get(username="guardian")
        self.assertEqual(guardian.school, self.school)

    def test_a_parent_cannot_use_the_student_code(self):
        response = self.register(
            username="guardian-bad",
            email="guardian-bad@example.com",
            role=Role.PARENT.value,
            phone_number="+27821234567",
            school=self.school.id,
            code=self.school.student_join_code,  # wrong code for a parent
        )
        self.assertIn("code", response.context["form"].errors)
        self.assertFalse(User.objects.filter(username="guardian-bad").exists())

    def test_the_main_form_does_not_offer_the_school_admin_role(self):
        response = self.register(
            username="sneaky-admin",
            email="sneaky@example.com",
            role=Role.SCHOOL_ADMIN.value,
            school=self.school.id,
            code=self.school.student_join_code,
        )
        self.assertIn("role", response.context["form"].errors)
        self.assertFalse(User.objects.filter(username="sneaky-admin").exists())
