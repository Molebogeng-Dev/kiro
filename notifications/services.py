"""Sending parent notifications about a marked paper.

Two responsibilities, kept apart on purpose:

``send_whatsapp`` is the thin transport shim over Twilio. It knows how to turn a
number and a body into a WhatsApp message and nothing else. It is the only place
that imports Twilio, and it imports it lazily so the dependency is only needed on
a machine that actually sends — a developer running the test suite never loads
it.

``notify_parents_of_marked_paper`` is the policy: who gets told, how the dedupe
guard works, and what gets recorded. It is deliberately forgiving — a failed
send is a recorded ``Notification``, not an exception — because its caller is the
marking engine, and marking a paper must never fail because a message did not go
out.
"""

import logging

from django.conf import settings

from marking.models import Paper

from .models import Notification
from .summary import generate_summary

logger = logging.getLogger(__name__)


class WhatsAppError(Exception):
    """Sending a WhatsApp message did not succeed."""


def _whatsapp_address(number: str) -> str:
    """Twilio wants ``whatsapp:+27...``; accept a bare number too."""
    return number if number.startswith("whatsapp:") else f"whatsapp:{number}"


def send_whatsapp(to_number: str, body: str) -> str:
    """Send one WhatsApp message and return the provider message SID.

    Raises :class:`WhatsAppError` on any misconfiguration or provider failure,
    so the caller can record the reason against the notification. Twilio is
    imported here, lazily, so nothing else in the app depends on it being
    installed.
    """
    if not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN):
        raise WhatsAppError("Twilio is not configured (missing SID or auth token).")
    if not settings.TWILIO_WHATSAPP_FROM:
        raise WhatsAppError("Twilio is not configured (missing WhatsApp sender).")

    try:
        from twilio.base.exceptions import TwilioRestException
        from twilio.rest import Client
    except ImportError as exc:  # pragma: no cover - environment problem, not logic.
        raise WhatsAppError(f"The twilio package is not installed: {exc}") from exc

    client = Client(
        settings.TWILIO_ACCOUNT_SID,
        settings.TWILIO_AUTH_TOKEN,
        # Bound the HTTP call so a slow provider cannot stretch the marking
        # request that triggered it.
        http_client=_timed_http_client(),
    )

    try:
        message = client.messages.create(
            from_=_whatsapp_address(settings.TWILIO_WHATSAPP_FROM),
            to=_whatsapp_address(to_number),
            body=body,
        )
    except TwilioRestException as exc:
        raise WhatsAppError(f"Twilio rejected the message: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - network, DNS, timeouts, anything.
        raise WhatsAppError(f"Could not reach Twilio: {exc}") from exc

    return message.sid


def _timed_http_client():
    from twilio.http.http_client import TwilioHttpClient

    return TwilioHttpClient(timeout=settings.TWILIO_TIMEOUT)


def notify_parents_of_marked_paper(paper) -> list[Notification]:
    """Notify every linked parent that ``paper`` has been marked.

    Only marked papers with a known student notify anyone: a failed marking
    attempt has nothing to summarise, and a paper with no student has no parent
    to reach. For each linked parent with a phone number that has not already
    been notified about this paper, generate a summary, attempt the send, and
    record a :class:`Notification` — ``sent`` on success, ``failed`` with the
    error otherwise.

    Returns the notifications created this call (an empty list if there was
    nobody new to tell). Never raises for a send failure; the record carries it.
    """
    if paper.status != Paper.Status.MARKED or paper.student_id is None:
        return []

    created = []
    for parent in paper.student.parents:
        # A parent who never gave a number cannot be reached; skip silently.
        if not parent.phone_number:
            continue

        # The dedupe guard. A re-marked paper must not message the same parent
        # twice; the unique constraint would reject the row anyway, but checking
        # first avoids generating a summary we would only throw away.
        if Notification.objects.filter(paper=paper, parent=parent).exists():
            continue

        created.append(_notify_one(paper, parent))

    return created


def _notify_one(paper, parent) -> Notification:
    """Summarise, send, and record one parent's notification for one paper."""
    summary = generate_summary(paper)

    try:
        send_whatsapp(parent.whatsapp_address, summary)
    except WhatsAppError as exc:
        logger.warning(
            "WhatsApp send failed for parent %s about paper %s: %s",
            parent.pk,
            paper.pk,
            exc,
        )
        return Notification.objects.create(
            paper=paper,
            parent=parent,
            summary_text=summary,
            status=Notification.Status.FAILED,
            error=str(exc)[:2000],
        )

    return Notification.objects.create(
        paper=paper,
        parent=parent,
        summary_text=summary,
        status=Notification.Status.SENT,
    )
