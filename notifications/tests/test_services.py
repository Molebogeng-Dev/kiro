"""Tests for the notification services.

Two layers, tested apart:

``send_whatsapp`` — the Twilio shim. Twilio is mocked; the test asserts on how
we call it and how we turn its failures into a ``WhatsAppError`` the caller can
record. No message ever leaves the machine.

``notify_parents_of_marked_paper`` — the policy. ``generate_summary`` and
``send_whatsapp`` are both mocked here, because who-gets-told, the dedupe guard,
and what-gets-recorded are separate from how a summary is written or a message
is sent.
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from marking.models import Paper
from notifications.models import Notification
from notifications.services import (
    WhatsAppError,
    notify_parents_of_marked_paper,
    send_whatsapp,
)

from .support import link, make_marked_paper, make_parent, make_student

TWILIO_SETTINGS = dict(
    TWILIO_ACCOUNT_SID="AC-test-sid",
    TWILIO_AUTH_TOKEN="test-token",
    TWILIO_WHATSAPP_FROM="+14155238886",
)


@override_settings(**TWILIO_SETTINGS)
class SendWhatsAppTests(TestCase):
    def _patch_client(self):
        """Patch the lazily-imported twilio Client, returning the mock message."""
        message = MagicMock(sid="SM-test-sid")
        client = MagicMock()
        client.messages.create.return_value = message
        client_class = MagicMock(return_value=client)
        return patch("twilio.rest.Client", client_class), client

    def test_a_send_returns_the_provider_message_id(self):
        patcher, client = self._patch_client()
        with patcher, patch("notifications.services._timed_http_client"):
            sid = send_whatsapp("whatsapp:+27821234567", "Hello")

        self.assertEqual(sid, "SM-test-sid")

    def test_the_message_is_addressed_with_the_whatsapp_prefix(self):
        patcher, client = self._patch_client()
        with patcher, patch("notifications.services._timed_http_client"):
            send_whatsapp("+27821234567", "Hello")  # bare number

        kwargs = client.messages.create.call_args.kwargs
        self.assertEqual(kwargs["to"], "whatsapp:+27821234567")
        self.assertEqual(kwargs["from_"], "whatsapp:+14155238886")
        self.assertEqual(kwargs["body"], "Hello")

    def test_a_provider_failure_becomes_a_whatsapp_error(self):
        patcher, client = self._patch_client()
        client.messages.create.side_effect = RuntimeError("network gone")
        with patcher, patch("notifications.services._timed_http_client"):
            with self.assertRaises(WhatsAppError):
                send_whatsapp("whatsapp:+27821234567", "Hello")

    @override_settings(TWILIO_ACCOUNT_SID="", TWILIO_AUTH_TOKEN="")
    def test_missing_credentials_raise_a_whatsapp_error(self):
        with self.assertRaises(WhatsAppError):
            send_whatsapp("whatsapp:+27821234567", "Hello")

    @override_settings(TWILIO_WHATSAPP_FROM="")
    def test_a_missing_sender_raises_a_whatsapp_error(self):
        with self.assertRaises(WhatsAppError):
            send_whatsapp("whatsapp:+27821234567", "Hello")


class NotifyParentsTests(TestCase):
    def setUp(self):
        self.student = make_student("notify-student")
        self.paper = make_marked_paper(self.student)

    def _run(self, *, send_side_effect=None):
        """Run the trigger with summary + send mocked. Returns the send mock."""
        with patch(
            "notifications.services.generate_summary",
            return_value="A summary a parent can read.",
        ), patch(
            "notifications.services.send_whatsapp",
            side_effect=send_side_effect,
            return_value="SM-1",
        ) as send:
            notify_parents_of_marked_paper(self.paper)
        return send

    def test_a_linked_parent_with_a_phone_is_notified(self):
        parent = make_parent("notify-parent")
        link(parent, self.student)

        send = self._run()

        send.assert_called_once()
        notification = Notification.objects.get(paper=self.paper, parent=parent)
        self.assertEqual(notification.status, Notification.Status.SENT)
        self.assertEqual(notification.summary_text, "A summary a parent can read.")
        self.assertEqual(notification.error, "")

    def test_the_message_goes_to_the_parents_whatsapp_address(self):
        parent = make_parent("addr-parent", phone_number="+27831112222")
        link(parent, self.student)

        send = self._run()

        self.assertEqual(send.call_args.args[0], "whatsapp:+27831112222")

    def test_a_parent_without_a_phone_number_is_skipped(self):
        parent = make_parent("no-phone-parent", phone_number=None)
        link(parent, self.student)

        send = self._run()

        send.assert_not_called()
        self.assertEqual(Notification.objects.count(), 0)

    def test_every_linked_parent_with_a_phone_is_notified(self):
        mom = make_parent("mom", phone_number="+27831112222")
        dad = make_parent("dad", phone_number="+27833334444")
        no_phone = make_parent("guardian", phone_number=None)
        for parent in (mom, dad, no_phone):
            link(parent, self.student)

        send = self._run()

        self.assertEqual(send.call_count, 2)
        self.assertEqual(Notification.objects.filter(paper=self.paper).count(), 2)
        self.assertFalse(
            Notification.objects.filter(parent=no_phone).exists()
        )

    def test_a_failed_marking_attempt_notifies_nobody(self):
        parent = make_parent("failed-paper-parent")
        link(parent, self.student)
        self.paper.status = Paper.Status.FAILED
        self.paper.save(update_fields=["status"])

        send = self._run()

        send.assert_not_called()
        self.assertEqual(Notification.objects.count(), 0)

    def test_a_paper_with_no_student_notifies_nobody(self):
        orphan = Paper.objects.get(pk=self.paper.pk)
        orphan.student = None
        orphan.save(update_fields=["student"])

        parent = make_parent("orphan-parent")
        # Nothing to link to; just prove the guard returns early.
        result = notify_parents_of_marked_paper(orphan)

        self.assertEqual(result, [])
        self.assertEqual(Notification.objects.count(), 0)

    def test_a_failed_send_is_recorded_without_raising(self):
        parent = make_parent("send-fails-parent")
        link(parent, self.student)

        # The send blows up, but the trigger must not.
        self._run(send_side_effect=WhatsAppError("Twilio refused the message"))

        notification = Notification.objects.get(paper=self.paper, parent=parent)
        self.assertEqual(notification.status, Notification.Status.FAILED)
        self.assertIn("Twilio refused", notification.error)

    def test_remarking_does_not_notify_the_same_parent_twice(self):
        parent = make_parent("dedupe-parent")
        link(parent, self.student)

        first = self._run()
        second = self._run()  # a second marking of the same paper

        first.assert_called_once()
        second.assert_not_called()  # nothing new to do the second time
        self.assertEqual(
            Notification.objects.filter(paper=self.paper, parent=parent).count(), 1
        )
