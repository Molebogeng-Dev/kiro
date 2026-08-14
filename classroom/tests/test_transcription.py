"""Tests for photographing assignment instructions into the form.

The service itself is tested in marking/tests/test_transcription.py; this file
covers the assignment view's use of it, which is the same review-before-save
pattern as memorandums applied to the instructions field.
"""

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from classroom.models import Assignment
from core.images import ImageValidationError
from marking.tests.support import make_memorandum, make_upload, make_user
from marking.transcription import TranscriptionError


class AssignmentTranscriptionViewTests(TestCase):
    def setUp(self):
        self.teacher = make_user("assign-transcribe-teacher", Role.TEACHER)
        self.memorandum = make_memorandum(created_by=self.teacher)
        self.client.force_login(self.teacher)
        self.url = reverse("classroom:assignment_create")

    def transcribe_post(self, returns=None, raises=None, with_image=True, **extra):
        data = {"action": "transcribe", "title": "", "memorandum": "", "due_date": ""}
        data.update(extra)
        if with_image:
            data["image"] = make_upload()

        with patch("classroom.views.transcribe_upload") as transcribe:
            if raises is not None:
                transcribe.side_effect = raises
            else:
                transcribe.return_value = (
                    returns or "Complete questions 1 to 10. Show your working."
                )
            self.transcribe = transcribe
            return self.client.post(self.url, data)

    def test_a_photo_prefills_the_instructions(self):
        response = self.transcribe_post(
            returns="Complete questions 1 to 10. Show your working clearly."
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Complete questions 1 to 10")

    def test_transcribing_saves_nothing(self):
        self.transcribe_post()
        self.assertFalse(Assignment.objects.filter(created_by=self.teacher).exists())

    def test_the_teacher_is_told_to_check_it(self):
        response = self.transcribe_post()
        self.assertContains(response, "Check the instructions")

    def test_typed_title_and_chosen_memorandum_survive(self):
        response = self.transcribe_post(
            title="Homework 3", memorandum=self.memorandum.pk
        )

        self.assertContains(response, "Homework 3")
        self.assertEqual(
            str(response.context["form"].initial["memorandum"]),
            str(self.memorandum.pk),
        )

    def test_without_a_photo_asks_for_one(self):
        response = self.transcribe_post(with_image=False)

        self.assertContains(response, "Choose a photo")
        self.assertFalse(Assignment.objects.filter(created_by=self.teacher).exists())

    def test_a_failure_is_shown_and_nothing_is_saved(self):
        response = self.transcribe_post(
            raises=TranscriptionError(
                "The reading service is busy right now. Wait a minute and try "
                "again, or type it in below."
            )
        )

        self.assertContains(response, "type it in below")
        self.assertFalse(Assignment.objects.filter(created_by=self.teacher).exists())

    def test_an_unreadable_photo_is_reported(self):
        response = self.transcribe_post(
            raises=ImageValidationError(
                "That file is not a readable image. Please upload a JPEG or PNG photo."
            )
        )
        self.assertContains(response, "not a readable image")

    def test_transcribe_then_save_posts_the_assignment(self):
        response = self.transcribe_post(
            returns="Complete questions 1 to 10 in your workbook. Show your working."
        )
        reviewed = response.context["form"].initial["instructions"]

        saved = self.client.post(
            self.url,
            {
                "action": "save",
                "title": "Homework 3",
                "memorandum": self.memorandum.pk,
                "instructions": reviewed,
                "due_date": "",
            },
        )

        self.assertRedirects(saved, reverse("classroom:assignment_list"))
        assignment = Assignment.objects.get(created_by=self.teacher)
        self.assertEqual(assignment.title, "Homework 3")
        self.assertIn("questions 1 to 10", assignment.instructions)

    def test_a_student_cannot_use_transcription(self):
        self.client.force_login(make_user("assign-transcribe-student", Role.STUDENT))

        with patch("classroom.views.transcribe_upload") as transcribe:
            transcribe.return_value = "x"
            response = self.client.post(
                self.url, {"action": "transcribe", "image": make_upload()}
            )

        self.assertEqual(response.status_code, 403)
        transcribe.assert_not_called()
