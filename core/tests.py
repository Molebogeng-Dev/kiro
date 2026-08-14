"""Tests for the Supabase Storage backend.

The response shapes here are copied from what the live Supabase API actually
returned, not from what the documentation implies. The important one is that a
missing object comes back as HTTP 400 carrying a 404 in its body, which is the
kind of detail that only shows up when you try it.
"""

from unittest.mock import patch

from django.core.files.base import ContentFile
from django.test import SimpleTestCase

from core.storage import (
    SupabaseStorage,
    SupabaseStorageError,
    SupabaseStorageNotConfigured,
)

# Verbatim shape of a Supabase "no such object" response.
NOT_FOUND_BODY = {
    "statusCode": "404",
    "error": "not_found",
    "message": "Object not found",
    "code": "NoSuchKey",
}


class FakeResponse:
    def __init__(self, status_code, json_body=None, content=b"", headers=None):
        self.status_code = status_code
        self._json_body = json_body
        self.content = content
        self.text = str(json_body or "")
        self.headers = headers or {}

    def json(self):
        if self._json_body is None:
            raise ValueError("no json")
        return self._json_body


def build_storage(**overrides):
    defaults = {
        "bucket": "papers",
        "base_url": "https://project.supabase.co",
        "service_key": "service-role-key",
    }
    return SupabaseStorage(**{**defaults, **overrides})


class ConfigurationTests(SimpleTestCase):
    """Missing credentials must fail with instructions, not a stack trace."""

    def test_every_missing_setting_is_named(self):
        storage = SupabaseStorage()

        with self.assertRaises(SupabaseStorageNotConfigured) as caught:
            storage.exists("papers/x.jpg")

        message = str(caught.exception)
        for setting in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SUPABASE_STORAGE_BUCKET"):
            self.assertIn(setting, message)

    def test_a_missing_service_key_is_reported_on_its_own(self):
        storage = build_storage(service_key="")

        with self.assertRaises(SupabaseStorageNotConfigured) as caught:
            storage.exists("papers/x.jpg")

        self.assertIn("SUPABASE_SERVICE_KEY", str(caught.exception))
        self.assertNotIn("SUPABASE_URL", str(caught.exception))

    def test_nothing_is_validated_at_construction(self):
        """check, migrate, and the test suite must all work with no credentials."""
        SupabaseStorage()  # must not raise


class UploadTests(SimpleTestCase):
    def test_a_save_posts_the_bytes_to_the_bucket_path(self):
        storage = build_storage()

        with patch("core.storage.requests.post") as post:
            post.return_value = FakeResponse(200, {"Key": "papers/2026/08/abc.jpg"})
            name = storage._save("papers/2026/08/abc.jpg", ContentFile(b"jpeg-bytes"))

        self.assertEqual(name, "papers/2026/08/abc.jpg")
        self.assertEqual(
            post.call_args.args[0],
            "https://project.supabase.co/storage/v1/object/papers/papers/2026/08/abc.jpg",
        )
        self.assertEqual(post.call_args.kwargs["data"], b"jpeg-bytes")
        self.assertEqual(
            post.call_args.kwargs["headers"]["Authorization"], "Bearer service-role-key"
        )

    def test_a_failed_upload_says_what_the_provider_said(self):
        storage = build_storage()

        with patch("core.storage.requests.post") as post:
            post.return_value = FakeResponse(403, {"message": "row-level security"})
            with self.assertRaises(SupabaseStorageError) as caught:
                storage._save("papers/x.jpg", ContentFile(b"bytes"))

        self.assertIn("403", str(caught.exception))

    def test_path_segments_are_escaped_but_slashes_are_kept(self):
        storage = build_storage()

        with patch("core.storage.requests.post") as post:
            post.return_value = FakeResponse(200, {})
            storage._save("papers/2026/08/a b.jpg", ContentFile(b"bytes"))

        self.assertTrue(post.call_args.args[0].endswith("/papers/2026/08/a%20b.jpg"))


class NotFoundHandlingTests(SimpleTestCase):
    """Supabase reports a missing object as 400-with-404-inside."""

    def test_deleting_a_missing_object_is_not_an_error(self):
        """Django calls delete() whenever a FileField is replaced."""
        storage = build_storage()

        with patch("core.storage.requests.delete") as delete:
            delete.return_value = FakeResponse(400, NOT_FOUND_BODY)
            storage.delete("papers/gone.jpg")  # must not raise

    def test_a_genuine_delete_failure_still_raises(self):
        storage = build_storage()

        with patch("core.storage.requests.delete") as delete:
            delete.return_value = FakeResponse(403, {"error": "unauthorized"})
            with self.assertRaises(SupabaseStorageError):
                storage.delete("papers/x.jpg")

    def test_a_successful_delete_is_accepted(self):
        storage = build_storage()

        for status_code in (200, 204):
            with self.subTest(status_code=status_code):
                with patch("core.storage.requests.delete") as delete:
                    delete.return_value = FakeResponse(status_code, {})
                    storage.delete("papers/x.jpg")

    def test_exists_is_false_for_a_missing_object(self):
        storage = build_storage()

        with patch("core.storage.requests.get") as get:
            get.return_value = FakeResponse(400, NOT_FOUND_BODY)
            self.assertFalse(storage.exists("papers/gone.jpg"))

    def test_exists_is_true_for_a_present_object(self):
        storage = build_storage()

        with patch("core.storage.requests.get") as get:
            get.return_value = FakeResponse(200, {"size": 1234})
            self.assertTrue(storage.exists("papers/here.jpg"))

    def test_exists_raises_rather_than_lying_when_the_check_fails(self):
        """Answering "no" to an unreadable object would let Django overwrite it."""
        storage = build_storage()

        with patch("core.storage.requests.get") as get:
            get.return_value = FakeResponse(500, {"error": "internal"})
            with self.assertRaises(SupabaseStorageError):
                storage.exists("papers/x.jpg")

    def test_opening_a_missing_object_raises_file_not_found(self):
        storage = build_storage()

        with patch("core.storage.requests.get") as get:
            get.return_value = FakeResponse(400, NOT_FOUND_BODY)
            with self.assertRaises(FileNotFoundError):
                storage._open("papers/gone.jpg")


class DownloadAndMetadataTests(SimpleTestCase):
    def test_open_returns_the_object_bytes(self):
        storage = build_storage()

        with patch("core.storage.requests.get") as get:
            get.return_value = FakeResponse(200, content=b"jpeg-bytes")
            self.assertEqual(storage._open("papers/x.jpg").read(), b"jpeg-bytes")

    def test_size_comes_from_the_info_endpoint(self):
        storage = build_storage()

        with patch("core.storage.requests.get") as get:
            get.return_value = FakeResponse(200, {"size": 4096})
            self.assertEqual(storage.size("papers/x.jpg"), 4096)

    def test_opening_for_writing_is_refused(self):
        storage = build_storage()

        with self.assertRaises(SupabaseStorageError):
            storage._open("papers/x.jpg", mode="wb")


class SignedUrlTests(SimpleTestCase):
    """Papers are private, so URLs are signed and expiring."""

    def test_a_signed_url_is_built_from_the_returned_path(self):
        storage = build_storage(signed_url_expiry=900)

        with patch("core.storage.requests.post") as post:
            post.return_value = FakeResponse(
                200, {"signedURL": "/object/sign/papers/x.jpg?token=abc123"}
            )
            url = storage.url("papers/x.jpg")

        self.assertEqual(
            url,
            "https://project.supabase.co/storage/v1/object/sign/papers/x.jpg?token=abc123",
        )
        self.assertEqual(post.call_args.kwargs["json"], {"expiresIn": 900})

    def test_a_signing_failure_is_reported(self):
        storage = build_storage()

        with patch("core.storage.requests.post") as post:
            post.return_value = FakeResponse(403, {"error": "denied"})
            with self.assertRaises(SupabaseStorageError):
                storage.url("papers/x.jpg")

    def test_a_response_with_no_signed_url_is_reported(self):
        storage = build_storage()

        with patch("core.storage.requests.post") as post:
            post.return_value = FakeResponse(200, {})
            with self.assertRaises(SupabaseStorageError):
                storage.url("papers/x.jpg")


class BucketTests(SimpleTestCase):
    def test_an_existing_bucket_is_left_alone(self):
        storage = build_storage()

        with patch("core.storage.requests.get") as get, patch("core.storage.requests.post") as post:
            get.return_value = FakeResponse(200, {"name": "papers"})
            self.assertFalse(storage.ensure_bucket())

        post.assert_not_called()

    def test_a_missing_bucket_is_created_private_by_default(self):
        storage = build_storage()

        with patch("core.storage.requests.get") as get, patch("core.storage.requests.post") as post:
            get.return_value = FakeResponse(404, NOT_FOUND_BODY)
            post.return_value = FakeResponse(200, {"name": "papers"})
            self.assertTrue(storage.ensure_bucket())

        self.assertEqual(post.call_args.kwargs["json"]["public"], False)

    def test_a_failure_to_create_the_bucket_is_reported(self):
        storage = build_storage()

        with patch("core.storage.requests.get") as get, patch("core.storage.requests.post") as post:
            get.return_value = FakeResponse(404, NOT_FOUND_BODY)
            post.return_value = FakeResponse(403, {"error": "denied"})
            with self.assertRaises(SupabaseStorageError):
                storage.ensure_bucket()
