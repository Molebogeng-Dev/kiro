"""Turning a phone photo into something worth sending over a slow connection.

Two constraints shape this module. The first is bandwidth: a learner
photographing homework is likely on mobile data they pay for by the megabyte, so
a 4 MB camera original has to become a few hundred kilobytes before it goes
anywhere. The second is legibility: compress too hard and the model can no
longer read the handwriting, which is worse than a slow upload. The defaults
below aim at the middle of that trade-off and are configurable in settings.
"""

import io
from dataclasses import dataclass

from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

# What a phone camera or a scanner app actually produces.
ALLOWED_FORMATS = {"JPEG", "PNG"}

# Everything is normalised to JPEG on the way out: it is the smallest of the
# formats we accept for photographs, and the model does not care.
OUTPUT_FORMAT = "JPEG"
OUTPUT_EXTENSION = "jpg"


class ImageValidationError(ValueError):
    """The upload is not an image we can work with. Safe to show to a user."""


@dataclass(frozen=True)
class ProcessedImage:
    """A validated, re-oriented, compressed image ready to store or send.

    Holds bytes rather than a file object on purpose. The same image gets both
    uploaded to storage and sent to the model, and a shared file pointer means
    whichever consumer runs second reads nothing.
    """

    data: bytes
    width: int
    height: int
    original_bytes: int
    processed_bytes: int
    original_format: str

    def as_content_file(self, name=None) -> ContentFile:
        """A fresh, independently-positioned file wrapper around the image."""
        return ContentFile(self.data, name=name)

    @property
    def bytes_saved(self) -> int:
        return max(self.original_bytes - self.processed_bytes, 0)

    @property
    def reduction_percent(self) -> float:
        """How much smaller the stored image is than what was uploaded."""
        if not self.original_bytes:
            return 0.0
        return round(self.bytes_saved / self.original_bytes * 100, 1)

    def as_dict(self) -> dict:
        return {
            "width": self.width,
            "height": self.height,
            "original_bytes": self.original_bytes,
            "stored_bytes": self.processed_bytes,
            "reduction_percent": self.reduction_percent,
            "original_format": self.original_format,
        }


def process_upload(
    uploaded_file,
    *,
    max_dimension: int,
    jpeg_quality: int,
    max_bytes: int,
) -> ProcessedImage:
    """Validate, auto-orient, and compress ``uploaded_file``.

    Raises ``ImageValidationError`` for anything that is not a usable JPEG or
    PNG, including files that merely claim to be one.
    """
    size = getattr(uploaded_file, "size", None)
    if size is not None and size > max_bytes:
        raise ImageValidationError(
            f"That image is {size / 1_048_576:.1f} MB. "
            f"Please keep it under {max_bytes / 1_048_576:.0f} MB."
        )

    uploaded_file.seek(0)
    raw = uploaded_file.read()
    if not raw:
        raise ImageValidationError("That file is empty.")
    if len(raw) > max_bytes:
        raise ImageValidationError(
            f"That image is {len(raw) / 1_048_576:.1f} MB. "
            f"Please keep it under {max_bytes / 1_048_576:.0f} MB."
        )

    original_format = _verify_is_supported_image(raw)

    # verify() consumes the file object, so reopen for the actual work.
    try:
        image = Image.open(io.BytesIO(raw))

        # Phone cameras record orientation in EXIF rather than rotating the
        # pixels. Without this, a photo taken in portrait arrives sideways and
        # the model has to read the handwriting rotated 90 degrees.
        image = ImageOps.exif_transpose(image)

        image = _flatten_to_rgb(image)

        if max(image.size) > max_dimension:
            image.thumbnail((max_dimension, max_dimension), Image.LANCZOS)

        buffer = io.BytesIO()
        image.save(buffer, format=OUTPUT_FORMAT, quality=jpeg_quality, optimize=True)
    except Image.DecompressionBombError as exc:
        raise ImageValidationError("That image is too large to process safely.") from exc
    except OSError as exc:
        raise ImageValidationError(f"That image could not be processed: {exc}") from exc

    processed = buffer.getvalue()

    return ProcessedImage(
        data=processed,
        width=image.width,
        height=image.height,
        original_bytes=len(raw),
        processed_bytes=len(processed),
        original_format=original_format,
    )


def _verify_is_supported_image(raw: bytes) -> str:
    """Confirm the bytes really are a JPEG or PNG, and say which."""
    try:
        probe = Image.open(io.BytesIO(raw))
        image_format = probe.format
        # verify() checks the file is internally consistent, which catches
        # truncated uploads and files renamed to .jpg to get past a filter.
        probe.verify()
    except Image.DecompressionBombError as exc:
        raise ImageValidationError("That image is too large to process safely.") from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageValidationError(
            "That file is not a readable image. Please upload a JPEG or PNG photo."
        ) from exc

    if image_format not in ALLOWED_FORMATS:
        raise ImageValidationError(
            f"{image_format or 'That format'} is not supported. "
            "Please upload a JPEG or PNG photo."
        )

    return image_format


def _flatten_to_rgb(image: Image.Image) -> Image.Image:
    """JPEG has no alpha channel, so composite transparency onto white."""
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        background = Image.new("RGB", image.size, (255, 255, 255))
        converted = image.convert("RGBA")
        background.paste(converted, mask=converted.split()[-1])
        return background

    if image.mode != "RGB":
        return image.convert("RGB")

    return image
