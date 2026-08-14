"""Tests for image validation, orientation, and compression.

Two of these matter more than they look. The orientation test is the difference
between the model reading handwriting the right way up or sideways, and the
compression test is the difference between a learner spending 4 MB of mobile data
on one homework photo or a few hundred kilobytes.
"""

import io

from django.test import SimpleTestCase
from PIL import Image

from core.images import ImageValidationError, process_upload

from .support import make_image_bytes, make_rotated_image_bytes, make_upload

DEFAULTS = {
    "max_dimension": 1600,
    "jpeg_quality": 80,
    "max_bytes": 15 * 1024 * 1024,
}


def process(upload, **overrides):
    return process_upload(upload, **{**DEFAULTS, **overrides})


class ValidationTests(SimpleTestCase):
    def test_a_real_jpeg_is_accepted(self):
        processed = process(make_upload())
        self.assertEqual(processed.original_format, "JPEG")

    def test_a_real_png_is_accepted_and_converted_to_jpeg(self):
        upload = make_upload(
            "paper.png", make_image_bytes(image_format="PNG"), "image/png"
        )
        processed = process(upload)

        self.assertEqual(processed.original_format, "PNG")
        self.assertEqual(Image.open(io.BytesIO(processed.data)).format, "JPEG")

    def test_a_text_file_renamed_to_jpg_is_rejected(self):
        """Trusting the extension or the content type would be a hole."""
        upload = make_upload("paper.jpg", b"this is definitely not an image")

        with self.assertRaises(ImageValidationError) as caught:
            process(upload)
        self.assertIn("not a readable image", str(caught.exception))

    def test_an_empty_file_is_rejected(self):
        with self.assertRaises(ImageValidationError):
            process(make_upload("paper.jpg", b""))

    def test_a_truncated_image_is_rejected(self):
        truncated = make_image_bytes()[: len(make_image_bytes()) // 3]
        with self.assertRaises(ImageValidationError):
            process(make_upload("paper.jpg", truncated))

    def test_an_unsupported_format_is_rejected_by_name(self):
        buffer = io.BytesIO()
        Image.new("RGB", (60, 60), (1, 2, 3)).save(buffer, format="BMP")

        with self.assertRaises(ImageValidationError) as caught:
            process(make_upload("paper.bmp", buffer.getvalue()))
        self.assertIn("BMP", str(caught.exception))

    def test_an_oversized_upload_is_rejected_before_processing(self):
        upload = make_upload(content=make_image_bytes(size=(1200, 1200)))

        with self.assertRaises(ImageValidationError) as caught:
            process(upload, max_bytes=1024)
        self.assertIn("MB", str(caught.exception))


class OrientationTests(SimpleTestCase):
    def test_an_exif_rotated_photo_is_straightened(self):
        """Phones tag orientation instead of rotating pixels."""
        upload = make_upload(content=make_rotated_image_bytes(size=(600, 1200)))

        processed = process(upload)

        # Orientation 6 means "rotate 90 degrees", so the stored image is landscape.
        self.assertEqual((processed.width, processed.height), (1200, 600))

    def test_an_unrotated_photo_keeps_its_dimensions(self):
        upload = make_upload(content=make_image_bytes(size=(800, 600)))
        processed = process(upload)
        self.assertEqual((processed.width, processed.height), (800, 600))


class CompressionTests(SimpleTestCase):
    def test_a_large_photo_is_scaled_down_to_the_limit(self):
        upload = make_upload(content=make_image_bytes(size=(4000, 3000)))

        processed = process(upload, max_dimension=1600)

        self.assertEqual(max(processed.width, processed.height), 1600)
        self.assertEqual((processed.width, processed.height), (1600, 1200))

    def test_a_small_photo_is_not_upscaled(self):
        upload = make_upload(content=make_image_bytes(size=(500, 400)))
        processed = process(upload, max_dimension=1600)
        self.assertEqual((processed.width, processed.height), (500, 400))

    def test_a_detailed_photo_gets_smaller(self):
        """A flat colour compresses to almost nothing either way, so use noise."""
        import random

        random.seed(1)
        image = Image.new("RGB", (1800, 1400))
        image.putdata(
            [
                (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
                for _ in range(1800 * 1400)
            ]
        )
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        original = buffer.getvalue()

        processed = process(make_upload("paper.png", original, "image/png"))

        self.assertLess(processed.processed_bytes, processed.original_bytes)
        self.assertGreater(processed.reduction_percent, 50)
        self.assertEqual(processed.original_bytes, len(original))

    def test_the_stored_image_is_still_a_readable_jpeg(self):
        processed = process(make_upload(content=make_image_bytes(size=(2000, 1500))))

        reopened = Image.open(io.BytesIO(processed.data))
        reopened.verify()
        self.assertEqual(reopened.format, "JPEG")

    def test_transparency_is_flattened_onto_white(self):
        buffer = io.BytesIO()
        Image.new("RGBA", (120, 120), (255, 0, 0, 0)).save(buffer, format="PNG")

        processed = process(make_upload("paper.png", buffer.getvalue(), "image/png"))
        reopened = Image.open(io.BytesIO(processed.data))

        self.assertEqual(reopened.mode, "RGB")
        self.assertEqual(reopened.getpixel((5, 5)), (255, 255, 255))

    def test_the_report_of_what_was_saved_is_consistent(self):
        processed = process(make_upload(content=make_image_bytes(size=(3000, 2000))))
        report = processed.as_dict()

        self.assertEqual(report["original_bytes"], processed.original_bytes)
        self.assertEqual(report["stored_bytes"], processed.processed_bytes)
        self.assertEqual(report["width"], processed.width)

    def test_content_files_are_independent(self):
        """Two consumers, one image: neither should get an empty read."""
        processed = process(make_upload())

        first = processed.as_content_file().read()
        second = processed.as_content_file().read()

        self.assertEqual(first, second)
        self.assertEqual(first, processed.data)
