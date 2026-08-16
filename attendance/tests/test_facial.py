"""Facial enrollment, per-period scans and their rollup, and the manual fallback."""

import json
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from attendance.models import Attendance, AttendanceScan, FaceEnrollment
from attendance.services import record_scan

from .support import descriptor, make_student, make_teacher


class EnrollmentTests(TestCase):
    def setUp(self):
        self.teacher = make_teacher("en-teacher")
        self.secondary = make_student("sec-kid", grade=9)
        self.primary = make_student("pri-kid", grade=4)
        self.client.force_login(self.teacher)
        self.url = reverse("attendance:enroll")

    def enroll(self, student, *, consent=True, desc=None):
        data = {"student": student.id, "descriptor": json.dumps(desc or descriptor(0.0))}
        if consent:
            data["consent_confirmed"] = "on"
        return self.client.post(self.url, data)

    def test_enrollment_requires_consent(self):
        response = self.enroll(self.secondary, consent=False)

        self.assertEqual(response.status_code, 200)
        self.assertIn("consent_confirmed", response.context["form"].errors)
        self.assertFalse(FaceEnrollment.objects.exists())

    def test_enrollment_stores_only_the_descriptor_with_consent(self):
        self.enroll(self.secondary, desc=descriptor(0.1))

        enrollment = FaceEnrollment.objects.get(student=self.secondary)
        self.assertTrue(enrollment.consent_confirmed)
        self.assertEqual(enrollment.enrolled_by, self.teacher)
        self.assertEqual(len(enrollment.descriptor), 128)
        # The model has nowhere to keep a photo — only the descriptor is stored.
        self.assertEqual(
            [f.name for f in FaceEnrollment._meta.get_fields() if f.name == "photo"],
            [],
        )

    def test_a_primary_student_cannot_be_enrolled(self):
        """Routing, direction one: grades 1-7 are never offered facial enrollment."""
        response = self.enroll(self.primary)

        self.assertEqual(response.status_code, 200)
        self.assertIn("student", response.context["form"].errors)
        self.assertFalse(FaceEnrollment.objects.filter(student=self.primary).exists())

    def test_re_enrolling_updates_rather_than_duplicates(self):
        self.enroll(self.secondary, desc=descriptor(0.0))
        self.enroll(self.secondary, desc=descriptor(0.2))

        enrollments = FaceEnrollment.objects.filter(student=self.secondary)
        self.assertEqual(enrollments.count(), 1)
        self.assertEqual(enrollments.first().descriptor, descriptor(0.2))

    def test_the_model_rejects_enrollment_without_consent(self):
        enrollment = FaceEnrollment(
            student=self.secondary, descriptor=descriptor(), consent_confirmed=False
        )
        with self.assertRaises(ValidationError):
            enrollment.full_clean()

    def test_the_model_rejects_a_primary_student(self):
        enrollment = FaceEnrollment(
            student=self.primary, descriptor=descriptor(), consent_confirmed=True
        )
        with self.assertRaises(ValidationError):
            enrollment.full_clean()


class CheckInMatchingTests(TestCase):
    def setUp(self):
        self.teacher = make_teacher("ci-teacher")
        self.s1 = make_student("match-one", grade=9)
        self.s2 = make_student("match-two", grade=11)
        self.client.force_login(self.teacher)
        self.url = reverse("attendance:check_in")
        # s1 enrolled near 0.0, s2 far away near 1.0, so a 0.0 capture is
        # unambiguously s1.
        FaceEnrollment.objects.create(
            student=self.s1, descriptor=descriptor(0.0), consent_confirmed=True
        )
        FaceEnrollment.objects.create(
            student=self.s2, descriptor=descriptor(1.0), consent_confirmed=True
        )

    def scan(self, desc):
        return self.client.post(self.url, {"descriptor": json.dumps(desc)})

    def test_a_matching_face_records_a_scan_and_marks_arrival(self):
        response = self.scan(descriptor(0.0))

        self.assertEqual(response.context["result"], "scan")
        self.assertEqual(response.context["matched"], self.s1)

        record = Attendance.objects.get(student=self.s1, date=timezone.localdate())
        self.assertEqual(record.method, Attendance.Method.FACIAL)
        self.assertIsNotNone(record.arrived_at)
        self.assertEqual(record.scans.count(), 1)
        scan = record.scans.first()
        self.assertEqual(scan.method, Attendance.Method.FACIAL)
        self.assertEqual(scan.recorded_by, self.teacher)

    def test_many_scans_keep_one_row_and_append_many_scans(self):
        self.scan(descriptor(0.0))
        self.scan(descriptor(0.0))
        self.scan(descriptor(0.0))

        # Many scans a day, but still exactly one Attendance row for the student.
        self.assertEqual(Attendance.objects.filter(student=self.s1).count(), 1)
        record = Attendance.objects.get(student=self.s1)
        self.assertEqual(record.scans.count(), 3)
        self.assertIsNotNone(record.arrived_at)
        self.assertIsNotNone(record.departed_at)

    def test_it_matches_the_nearest_enrolled_student(self):
        self.scan(descriptor(0.0))
        self.assertTrue(Attendance.objects.filter(student=self.s1).exists())
        self.assertFalse(Attendance.objects.filter(student=self.s2).exists())

    def test_an_unrecognised_face_records_nothing_and_offers_the_fallback(self):
        response = self.scan(descriptor(0.5))  # distance ~5.7, well past threshold

        self.assertEqual(response.context["result"], "no_match")
        self.assertEqual(Attendance.objects.count(), 0)
        self.assertEqual(AttendanceScan.objects.count(), 0)
        # The manual fallback form is on the page, not a dead end.
        self.assertContains(response, "Mark present by hand")

    def test_a_malformed_descriptor_is_handled_without_crashing(self):
        response = self.client.post(self.url, {"descriptor": "not-a-descriptor"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Attendance.objects.count(), 0)
        self.assertEqual(AttendanceScan.objects.count(), 0)

    def test_a_wrong_length_descriptor_is_rejected(self):
        response = self.client.post(self.url, {"descriptor": json.dumps([0.0, 0.1])})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Attendance.objects.count(), 0)


class ScanRollupTests(TestCase):
    """The Attendance row is a rollup of the day's scans (service level).

    Timestamps are set explicitly so arrival-on-first and departure-follows-
    latest can be asserted precisely, which the HTTP path cannot control.
    """

    def setUp(self):
        self.teacher = make_teacher("roll-teacher")
        self.student = make_student("roll-kid", grade=9)
        self.t1 = timezone.now()
        self.t2 = self.t1 + timedelta(hours=2)
        self.t3 = self.t1 + timedelta(hours=4)

    def scan(self, when, method=Attendance.Method.FACIAL):
        return record_scan(
            self.student, method=method, recorded_by=self.teacher, when=when
        )

    def test_the_first_scan_sets_both_arrival_and_latest(self):
        attendance, scan, first = self.scan(self.t1)

        self.assertTrue(first)
        self.assertEqual(attendance.arrived_at, self.t1)
        self.assertEqual(attendance.departed_at, self.t1)
        self.assertEqual(scan.timestamp, self.t1)

    def test_arrival_stays_on_the_first_scan_and_latest_follows(self):
        self.scan(self.t1)
        self.scan(self.t2)
        self.scan(self.t3)

        attendance = Attendance.objects.get(student=self.student)
        self.assertEqual(attendance.arrived_at, self.t1)   # never moves off the first
        self.assertEqual(attendance.departed_at, self.t3)  # tracks the most recent

    def test_every_scan_is_recorded_against_one_row(self):
        for when in (self.t1, self.t2, self.t3):
            self.scan(when)

        self.assertEqual(Attendance.objects.filter(student=self.student).count(), 1)
        self.assertEqual(AttendanceScan.objects.filter(student=self.student).count(), 3)

    def test_first_today_is_true_only_for_the_first_scan(self):
        _, _, first_one = self.scan(self.t1)
        _, _, first_two = self.scan(self.t2)

        self.assertTrue(first_one)
        self.assertFalse(first_two)

    def test_facial_and_manual_scans_share_the_same_rollup(self):
        self.scan(self.t1, method=Attendance.Method.FACIAL)
        self.scan(self.t2, method=Attendance.Method.MANUAL)

        attendance = Attendance.objects.get(student=self.student)
        self.assertEqual(attendance.arrived_at, self.t1)
        self.assertEqual(attendance.departed_at, self.t2)
        self.assertEqual(attendance.scans.count(), 2)
        self.assertEqual(
            set(attendance.scans.values_list("method", flat=True)),
            {"facial", "manual"},
        )

    def test_a_manual_scan_needs_no_arrival_or_departure_hint(self):
        """A failed match just records a manual scan; no event type is passed."""
        attendance, scan, _ = record_scan(
            self.student, method=Attendance.Method.MANUAL, recorded_by=self.teacher
        )
        self.assertEqual(scan.method, "manual")
        self.assertIsNotNone(attendance.arrived_at)


class ManualFallbackTests(TestCase):
    """The no-match fallback records a manual-method scan, like any other scan."""

    def setUp(self):
        self.teacher = make_teacher("fb-teacher")
        self.secondary = make_student("fb-secondary", grade=10)
        self.primary = make_student("fb-primary", grade=3)
        self.client.force_login(self.teacher)
        self.url = reverse("attendance:mark_present")

    def test_the_fallback_records_a_manual_scan(self):
        self.client.post(self.url, {"student": self.secondary.id})

        record = Attendance.objects.get(student=self.secondary, date=timezone.localdate())
        self.assertEqual(record.method, Attendance.Method.MANUAL)
        self.assertIsNotNone(record.arrived_at)
        self.assertEqual(record.scans.count(), 1)
        scan = record.scans.first()
        self.assertEqual(scan.method, Attendance.Method.MANUAL)
        self.assertEqual(scan.recorded_by, self.teacher)

    def test_the_fallback_will_not_mark_a_primary_student(self):
        self.client.post(self.url, {"student": self.primary.id})
        self.assertFalse(Attendance.objects.filter(student=self.primary).exists())
        self.assertFalse(AttendanceScan.objects.filter(student=self.primary).exists())

    def test_the_fallback_requires_a_student(self):
        response = self.client.post(self.url, {}, follow=True)
        self.assertEqual(Attendance.objects.count(), 0)
        self.assertEqual(response.status_code, 200)

    def test_the_fallback_is_post_only(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
