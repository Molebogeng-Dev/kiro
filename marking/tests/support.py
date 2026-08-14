"""Shared test helpers.

Nothing here touches the network. Every OpenRouter interaction in the suite goes
through ``FakeResponse``, and storage is in-memory, configured in settings when
the test runner is active.
"""

import io
import json

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from accounts.models import Role, User
from marking.models import Memorandum

PASSWORD = "marking-tests-passphrase-77"

MEMORANDUM_TEXT = """Question 1.1 (2 marks)
Expected: 12

Question 1.2 (4 marks)
Expected: 48, with method shown
"""

# What a well-behaved reply looks like.
VALID_MARKING = {
    "overall": {"marks_awarded": 4, "marks_available": 6},
    "summary": "Solid understanding of multiplication, let down by one arithmetic slip.",
    "questions": [
        {
            "number": "1.1",
            "marks_awarded": 2,
            "marks_available": 2,
            "feedback": "Correct, and the working is clearly set out.",
        },
        {
            "number": "1.2",
            "marks_awarded": 2,
            "marks_available": 4,
            "feedback": (
                "The method is right but 6 x 8 was written as 42, so the final "
                "answer is wrong. Practise the six times table."
            ),
        },
    ],
}


def valid_marking_json(**overrides) -> str:
    payload = {**VALID_MARKING, **overrides}
    return json.dumps(payload)


def make_user(username, role=Role.TEACHER, **extra):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=PASSWORD,
        role=role,
        **extra,
    )


def make_memorandum(**overrides):
    defaults = {
        "title": "Grade 4 Mathematics Test 1",
        "subject": "Mathematics",
        "content": MEMORANDUM_TEXT,
        "total_marks": 6,
    }
    return Memorandum.objects.create(**{**defaults, **overrides})


def make_image_bytes(size=(1200, 900), image_format="JPEG", colour=(250, 250, 250)):
    """A real, decodable image, so Pillow validation is genuinely exercised."""
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format=image_format)
    return buffer.getvalue()


def make_rotated_image_bytes(size=(600, 1200), orientation=6):
    """An image whose EXIF says it should be rotated, like a phone photo."""
    buffer = io.BytesIO()
    image = Image.new("RGB", size, (240, 240, 240))
    exif = image.getexif()
    exif[274] = orientation  # 274 is the EXIF Orientation tag.
    image.save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


def make_upload(name="paper.jpg", content=None, content_type="image/jpeg"):
    return SimpleUploadedFile(
        name, content if content is not None else make_image_bytes(), content_type
    )


class FakeResponse:
    """Stand-in for ``requests.Response``."""

    def __init__(self, status_code=200, json_body=None, text=None, headers=None):
        self.status_code = status_code
        self._json_body = json_body
        self.text = text if text is not None else json.dumps(json_body or {})
        self.headers = headers or {}

    def json(self):
        if self._json_body is None:
            raise ValueError("No JSON body")
        return self._json_body


def completion_response(content, model="qwen/qwen2.5-vl-72b-instruct"):
    """A 200 from OpenRouter carrying ``content`` as the assistant message."""
    return FakeResponse(
        200,
        {
            "id": "gen-test",
            "model": model,
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"total_tokens": 1234},
        },
    )


def error_response(status_code, message="something went wrong", headers=None):
    return FakeResponse(
        status_code, {"error": {"message": message, "code": status_code}}, headers=headers
    )
