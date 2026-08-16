"""Matching a captured face descriptor against enrolled ones, server-side.

face-api.js can match in the browser, but that would mean shipping every
enrolled student's biometric descriptor to whatever device is running the
check-in station. These are children's face vectors, so they stay on the
server: the browser computes the descriptor for the captured frame and sends
only that, and the comparison happens here. The check-in station is operated by
a teacher (the views are teacher-only), so a teacher-supplied descriptor is an
acceptable trust boundary.

The comparison is face-api.js's own: euclidean distance between 128-float
descriptors, a match when the nearest enrolled descriptor is within a threshold.
"""

import math
from dataclasses import dataclass

# face-api.js FaceRecognitionNet produces 128-dimension descriptors.
DESCRIPTOR_LENGTH = 128


class InvalidDescriptor(ValueError):
    """The supplied descriptor is not a usable face vector."""


@dataclass(frozen=True)
class Match:
    enrollment: object  # a FaceEnrollment
    distance: float


def parse_descriptor(raw):
    """Validate untrusted input into a list of floats, or raise.

    Accepts a list/tuple of numbers (optionally the ``{"0": .., "1": ..}`` shape
    some JSON encoders produce for a Float32Array). Rejects anything else so a
    malformed post cannot reach the distance maths.
    """
    if isinstance(raw, dict):
        # Reassemble an object keyed by stringified indices, in index order.
        try:
            raw = [raw[key] for key in sorted(raw, key=int)]
        except (ValueError, TypeError) as exc:
            raise InvalidDescriptor("Descriptor object keys must be integers.") from exc

    if not isinstance(raw, (list, tuple)):
        raise InvalidDescriptor("Descriptor must be a list of numbers.")

    if len(raw) != DESCRIPTOR_LENGTH:
        raise InvalidDescriptor(
            f"Descriptor must have {DESCRIPTOR_LENGTH} values, got {len(raw)}."
        )

    try:
        vector = [float(value) for value in raw]
    except (TypeError, ValueError) as exc:
        raise InvalidDescriptor("Descriptor values must all be numbers.") from exc

    if any(math.isnan(value) or math.isinf(value) for value in vector):
        raise InvalidDescriptor("Descriptor values must be finite.")

    return vector


def euclidean_distance(a, b) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def best_match(descriptor, enrollments, *, threshold):
    """Return the closest enrollment within ``threshold``, or ``None``.

    ``descriptor`` must already be parsed. Enrollments whose stored descriptor
    is the wrong shape are skipped rather than crashing the whole check-in.
    """
    best = None
    for enrollment in enrollments:
        stored = enrollment.descriptor
        if not isinstance(stored, (list, tuple)) or len(stored) != len(descriptor):
            continue
        distance = euclidean_distance(descriptor, stored)
        if best is None or distance < best.distance:
            best = Match(enrollment=enrollment, distance=distance)

    if best is not None and best.distance <= threshold:
        return best
    return None
