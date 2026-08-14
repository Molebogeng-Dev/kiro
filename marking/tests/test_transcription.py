"""Tests for photographing a document into editable text.

Two layers, mirroring how the rest of the app is tested: the transcription
service is exercised against a mocked HTTP boundary (real image pipeline, fake
OpenRouter), and the memorandum view is exercised with the service itself mocked,
so each layer's logic is checked without the other in the way. No real API quota
is spent.

The theme running through the view tests is that transcription never saves
anything on its own and never leaves the teacher stuck: whatever the model does,
the fallback of typing it in is always there.
"""

from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from accounts.models import Role
from core.images import ImageValidationError
from marking.models import Memorandum, Paper
from marking.transcription import (
    TranscriptionError,
    TranscriptionKind,
    transcribe_document,
    transcribe_upload,
)

from .support import (
    completion_response,
    error_response,
    make_image_bytes,
    make_upload,
    make_user,
)


class TranscriptionServiceTests(SimpleTestCase):
    """The service turns a photo into text, and turns failures into advice."""

    def transcribe(self, response, kind=TranscriptionKind.MEMORANDUM):
        with patch("marking.openrouter.requests.post") as post:
            post.return_value = response
            self.post = post
            return transcribe_document(image_bytes=b"jpeg-bytes", kind=kind)

    def test_returns_the_transcribed_text(self):
        text = self.transcribe(
            completion_response("Question 1 (2 marks)\nExpected answer: 42")
        )
        self.assertEqual(text, "Question 1 (2 marks)\nExpected answer: 42")

    def test_code_fences_are_stripped(self):
        text = self.transcribe(
            completion_response("```\nQuestion 1\nExpected: 42\n```")
        )
        self.assertEqual(text, "Question 1\nExpected: 42")

    def test_a_reply_with_no_text_is_rejected(self):
        with self.assertRaises(TranscriptionError) as caught:
            self.transcribe(completion_response("```\n\n```"))
        self.assertIn("couldn't make out", str(caught.exception).lower())

    def test_a_rate_limit_becomes_a_friendly_message(self):
        with self.assertRaises(TranscriptionError) as caught:
            self.transcribe(error_response(429, "slow down"))
        message = str(caught.exception).lower()
        self.assertIn("busy", message)
        self.assertIn("type it in", message)

    def test_missing_credit_is_explained_in_plain_words(self):
        with self.assertRaises(TranscriptionError) as caught:
            self.transcribe(error_response(402, "no credit"))
        self.assertIn("credit", str(caught.exception).lower())

    def test_a_withdrawn_model_is_explained(self):
        with self.assertRaises(TranscriptionError) as caught:
            self.transcribe(error_response(404, "no such model"))
        self.assertIn("unavailable", str(caught.exception).lower())

    def test_no_failure_message_names_the_provider_or_a_status_code(self):
        for status in (429, 402, 404, 500):
            with self.subTest(status=status):
                with self.assertRaises(TranscriptionError) as caught:
                    self.transcribe(error_response(status))
                message = str(caught.exception)
                self.assertNotIn("OpenRouter", message)
                self.assertNotIn(str(status), message)

    def test_the_memorandum_and_assignment_prompts_differ(self):
        from marking.transcription import _USER_PROMPTS

        self.assertNotEqual(
            _USER_PROMPTS[TranscriptionKind.MEMORANDUM],
            _USER_PROMPTS[TranscriptionKind.ASSIGNMENT],
        )


class TranscribeUploadTests(SimpleTestCase):
    """The upload wrapper validates and compresses before sending."""

    def test_a_non_image_is_rejected_before_any_call(self):
        with patch("marking.openrouter.requests.post") as post:
            with self.assertRaises(ImageValidationError):
                transcribe_upload(
                    make_upload("memo.jpg", b"not an image at all"),
                    kind=TranscriptionKind.MEMORANDUM,
                )
            post.assert_not_called()

    def test_a_real_image_is_compressed_and_sent_as_a_data_uri(self):
        with patch("marking.openrouter.requests.post") as post:
            post.return_value = completion_response("Question 1\nExpected: 42")
            text = transcribe_upload(
                make_upload(content=make_image_bytes(size=(2400, 1800))),
                kind=TranscriptionKind.MEMORANDUM,
            )

        self.assertIn("Question 1", text)
        content = post.call_args.kwargs["json"]["messages"][1]["content"]
        data_uri = next(p for p in content if p["type"] == "image_url")["image_url"]["url"]
        self.assertTrue(data_uri.startswith("data:image/jpeg;base64,"))


class MemorandumTranscriptionViewTests(TestCase):
    """The memorandum form's "read from a photo" path."""

    def setUp(self):
        self.teacher = make_user("transcribe-teacher", Role.TEACHER)
        self.client.force_login(self.teacher)
        self.url = reverse("marking:memorandum_create")

    def transcribe_post(self, returns=None, raises=None, with_image=True, **extra):
        data = {"action": "transcribe", "title": "", "subject": "", "total_marks": ""}
        data.update(extra)
        if with_image:
            data["image"] = make_upload()

        with patch("marking.views.transcribe_upload") as transcribe:
            if raises is not None:
                transcribe.side_effect = raises
            else:
                transcribe.return_value = returns or "Question 1 (2 marks)\nExpected: 42"
            self.transcribe = transcribe
            return self.client.post(self.url, data)

    def test_a_photo_prefills_the_content_for_review(self):
        response = self.transcribe_post(returns="Question 1 (2 marks)\nExpected: 42")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Question 1 (2 marks)")

    def test_transcribing_saves_nothing(self):
        self.transcribe_post()

        self.assertFalse(Memorandum.objects.filter(created_by=self.teacher).exists())
        # And no paper or stored image was created as a side effect.
        self.assertEqual(Paper.objects.count(), 0)

    def test_the_teacher_is_told_to_check_it(self):
        response = self.transcribe_post()
        self.assertContains(response, "Check it carefully")

    def test_a_typed_title_survives_transcription(self):
        response = self.transcribe_post(title="Grade 4 Term 3 Test")
        self.assertContains(response, "Grade 4 Term 3 Test")

    def test_transcribing_without_a_photo_asks_for_one(self):
        response = self.transcribe_post(with_image=False)

        self.assertContains(response, "Choose a photo")
        self.assertFalse(Memorandum.objects.filter(created_by=self.teacher).exists())

    def test_a_service_failure_is_shown_and_nothing_is_saved(self):
        response = self.transcribe_post(
            raises=TranscriptionError(
                "The reading service is busy right now. Wait a minute and try "
                "again, or type it in below."
            )
        )

        self.assertContains(response, "type it in below")
        self.assertNotContains(response, "Traceback")
        self.assertFalse(Memorandum.objects.filter(created_by=self.teacher).exists())

    def test_an_unreadable_photo_is_reported(self):
        response = self.transcribe_post(
            raises=ImageValidationError(
                "That file is not a readable image. Please upload a JPEG or PNG photo."
            )
        )
        self.assertContains(response, "not a readable image")

    def test_transcribe_then_save_creates_the_memorandum(self):
        """The two-step flow: read a photo, review, then save."""
        response = self.transcribe_post(
            returns="Question 1 (2 marks)\nExpected: 42. Show the working."
        )
        reviewed = response.context["form"].initial["content"]

        saved = self.client.post(
            self.url,
            {
                "action": "save",
                "title": "Reviewed memo",
                "subject": "Mathematics",
                "total_marks": "",
                "content": reviewed,
            },
        )

        self.assertRedirects(saved, reverse("marking:memorandum_list"))
        memorandum = Memorandum.objects.get(created_by=self.teacher)
        self.assertEqual(memorandum.title, "Reviewed memo")
        self.assertIn("Question 1", memorandum.content)

    def test_a_student_cannot_use_transcription(self):
        self.client.force_login(make_user("transcribe-student", Role.STUDENT))

        with patch("marking.views.transcribe_upload") as transcribe:
            transcribe.return_value = "x"
            response = self.client.post(
                self.url, {"action": "transcribe", "image": make_upload()}
            )

        self.assertEqual(response.status_code, 403)
        transcribe.assert_not_called()
