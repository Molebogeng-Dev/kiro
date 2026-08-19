"""The marking engine triggers a parent notification, and nothing else changes.

These are the end-to-end behaviours the sprint's definition of done names: a
successful mark notifies linked parents, a failed mark notifies nobody, and a
problem in notifying never touches the paper the teacher and student can see.

The engine is run for real against a scripted OpenRouter reply (mocked at the
HTTP boundary, as the rest of the marking suite does). The summary and Twilio
calls are mocked — no network, no messages.
"""

from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from marking.engine import mark_paper
from marking.models import Paper
from marking.tests.support import completion_response, error_response, valid_marking_json
from notifications.models import Notification

from .support import link, make_memorandum, make_parent, make_student, make_teacher


class MarkingTriggerTests(TestCase):
    def setUp(self):
        self.student = make_student("trigger-student", first_name="Ayanda")
        self.parent = make_parent("trigger-parent")
        link(self.parent, self.student)

        self.paper = Paper.objects.create(
            memorandum=make_memorandum(),
            submitted_by=make_teacher("trigger-teacher"),
            student=self.student,
            image=SimpleUploadedFile("p.jpg", b"stub", "image/jpeg"),
        )

    def _mark(self, *responses):
        """Mark the paper against scripted HTTP replies, with sending mocked."""
        with patch("marking.openrouter.requests.post") as post, patch(
            "notifications.services.generate_summary",
            return_value="Ayanda's test has been marked.",
        ), patch(
            "notifications.services.send_whatsapp", return_value="SM-1"
        ) as send:
            post.side_effect = list(responses)
            try:
                return mark_paper(self.paper, image_bytes=b"jpeg-bytes")
            finally:
                self.send = send

    def test_a_successful_mark_notifies_the_linked_parent(self):
        self._mark(completion_response(valid_marking_json()))

        self.send.assert_called_once()
        notification = Notification.objects.get(paper=self.paper, parent=self.parent)
        self.assertEqual(notification.status, Notification.Status.SENT)

    def test_a_failed_mark_notifies_nobody(self):
        with patch("marking.openrouter.time.sleep"):
            with self.assertRaises(Exception):
                self._mark(error_response(429))

        self.assertEqual(Notification.objects.count(), 0)
        self.paper.refresh_from_db()
        self.assertEqual(self.paper.status, Paper.Status.FAILED)

    def test_a_notification_failure_does_not_affect_the_marked_paper(self):
        # The whole notification step blows up; the mark must still stand.
        with patch("marking.openrouter.requests.post") as post, patch(
            "notifications.services.notify_parents_of_marked_paper",
            side_effect=RuntimeError("notifications are on fire"),
        ):
            post.return_value = completion_response(valid_marking_json())
            result = mark_paper(self.paper, image_bytes=b"jpeg-bytes")

        # The result came back and the paper is marked, despite the explosion.
        self.assertIsNotNone(result)
        self.paper.refresh_from_db()
        self.assertEqual(self.paper.status, Paper.Status.MARKED)

    def test_remarking_a_paper_does_not_resend_to_the_same_parent(self):
        self._mark(completion_response(valid_marking_json()))
        self._mark(completion_response(valid_marking_json()))  # re-mark

        self.assertEqual(
            Notification.objects.filter(paper=self.paper, parent=self.parent).count(),
            1,
        )
