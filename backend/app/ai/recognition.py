"""Compare one frame with enrolled face embeddings.

The steps stay separate: detect every face, encode each face, compare an
embedding with the gallery, then accept a match only when the Euclidean
distance is within the configured threshold. This module does not read
the database and does not write attendance.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from app.ai.faces import (
    EMBEDDING_SIZE,
    Detector,
    EmbeddingFailed,
    Encoder,
    FaceBox,
    compact_embedding,
    detect_faces,
    encode_face,
    frame_to_rgb,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EnrolledEmbedding:
    """One active sample already stored for a student."""

    student_id: int
    embedding: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class EmbeddingIndex:
    """Gallery laid out for one distance pass per probe.

    `matrix` has one row per sample. Several rows may share a student id
    when that student has more than one active sample.
    """

    entries: tuple[EnrolledEmbedding, ...]
    student_ids: np.ndarray
    matrix: np.ndarray

    @classmethod
    def build(cls, entries: Sequence[EnrolledEmbedding]) -> EmbeddingIndex:
        """Copy gallery vectors into one numeric matrix."""
        cleaned: list[EnrolledEmbedding] = []
        for entry in entries:
            vector = tuple(compact_embedding(entry.embedding))
            cleaned.append(EnrolledEmbedding(student_id=int(entry.student_id), embedding=vector))
        if not cleaned:
            student_ids = np.zeros((0,), dtype=np.int64)
            matrix = np.zeros((0, EMBEDDING_SIZE), dtype=np.float64)
        else:
            student_ids = np.asarray([entry.student_id for entry in cleaned], dtype=np.int64)
            matrix = np.asarray([entry.embedding for entry in cleaned], dtype=np.float64)
        student_ids.setflags(write=False)
        matrix.setflags(write=False)
        return cls(entries=tuple(cleaned), student_ids=student_ids, matrix=matrix)

    @property
    def sample_count(self) -> int:
        return int(self.matrix.shape[0])

    @property
    def student_count(self) -> int:
        return len(set(int(student_id) for student_id in self.student_ids))


@dataclass(frozen=True, slots=True)
class EncodedFace:
    """A detected face and the embedding produced for it, if encoding worked."""

    box: FaceBox
    embedding: tuple[float, ...] | None


@dataclass(frozen=True, slots=True)
class Comparison:
    """Nearest enrolled student. The threshold has not been applied."""

    student_id: int
    distance: float


@dataclass(frozen=True, slots=True)
class FaceMatch:
    """Best student for one face, or a rejection.

    `student_id` is set only when `matched` is true. `confidence` is
    `1 - distance`, clamped to the range 0 through 1. `distance` is the
    Euclidean distance to the nearest sample of the nearest student.
    """

    student_id: int | None
    matched: bool
    confidence: float
    distance: float | None

    def as_dict(self) -> dict[str, object]:
        """Return the public recognition fields."""
        return {
            "student_id": self.student_id,
            "matched": self.matched,
            "confidence": self.confidence,
            "distance": self.distance,
        }


@dataclass(frozen=True, slots=True)
class RecognitionResult:
    """Best match in a frame, plus one result for every detected face.

    `as_dict` is the best match only. `faces` is empty when the frame has
    no face. A frame with several faces still returns one best match.
    """

    student_id: int | None
    matched: bool
    confidence: float
    distance: float | None
    faces: tuple[FaceMatch, ...]

    def as_dict(self) -> dict[str, object]:
        """Return the best match in the public recognition shape."""
        return {
            "student_id": self.student_id,
            "matched": self.matched,
            "confidence": self.confidence,
            "distance": self.distance,
        }

    @classmethod
    def from_faces(cls, faces: Sequence[FaceMatch]) -> RecognitionResult:
        chosen = best_match(faces)
        return cls(
            student_id=chosen.student_id,
            matched=chosen.matched,
            confidence=chosen.confidence,
            distance=chosen.distance,
            faces=tuple(faces),
        )


def detect_faces_in_frame(
    image_rgb: np.ndarray,
    *,
    detect: Detector | None = None,
) -> list[FaceBox]:
    """Find every face. An empty list means the frame has no face."""
    return list((detect or detect_faces)(image_rgb))


def encode_detected_faces(
    image_rgb: np.ndarray,
    boxes: Sequence[FaceBox],
    *,
    encode: Encoder | None = None,
) -> list[EncodedFace]:
    """Build one embedding per detected face.

    A face that cannot be encoded is kept, with `embedding` left empty, so
    the rest of the frame is still matched.
    """
    encoder = encode or encode_face
    encoded: list[EncodedFace] = []
    for box in boxes:
        try:
            embedding = tuple(compact_embedding(encoder(image_rgb, box)))
        except EmbeddingFailed:
            logger.warning(
                "Face encoding failed at top=%s right=%s bottom=%s left=%s",
                box.top,
                box.right,
                box.bottom,
                box.left,
            )
            encoded.append(EncodedFace(box=box, embedding=None))
            continue
        encoded.append(EncodedFace(box=box, embedding=embedding))
    return encoded


def compare_embedding(probe: Sequence[float], gallery: EmbeddingIndex) -> Comparison | None:
    """Return the nearest student. Several samples use the closest sample.

    The result does not apply the recognition threshold. An empty gallery
    returns None.
    """
    if gallery.sample_count == 0:
        return None
    vector = np.asarray(compact_embedding(probe), dtype=np.float64)
    distances = np.linalg.norm(gallery.matrix - vector, axis=1)
    best_distance: dict[int, float] = {}
    for student_id, distance in zip(gallery.student_ids, distances, strict=True):
        sid = int(student_id)
        value = float(distance)
        previous = best_distance.get(sid)
        if previous is None or value < previous:
            best_distance[sid] = value
    student_id, distance = min(best_distance.items(), key=lambda item: (item[1], item[0]))
    return Comparison(student_id=student_id, distance=distance)


def match_embedding(
    probe: Sequence[float],
    gallery: EmbeddingIndex,
    *,
    threshold: float,
) -> FaceMatch:
    """Accept the nearest student only when the distance is within the threshold."""
    _require_threshold(threshold)
    comparison = compare_embedding(probe, gallery)
    if comparison is None:
        logger.debug("No enrolled embeddings to compare")
        return _unmatched()
    distance, confidence = _distance_and_confidence(comparison.distance)
    if distance <= threshold:
        logger.debug(
            "Accepted face match: student_id=%s distance=%s threshold=%s",
            comparison.student_id,
            distance,
            threshold,
        )
        return FaceMatch(
            student_id=comparison.student_id,
            matched=True,
            confidence=confidence,
            distance=distance,
        )
    logger.info(
        "Rejected low-confidence face match: closest_student_id=%s distance=%s threshold=%s",
        comparison.student_id,
        distance,
        threshold,
    )
    return _unmatched(distance)


def best_match(faces: Sequence[FaceMatch]) -> FaceMatch:
    """Choose the accepted face with the smallest distance.

    Equal distances keep the smaller student id. When every face is
    rejected, the result stays unmatched and reports the closest distance
    without a student id. No faces produce an empty unmatched result.
    """
    if not faces:
        return _unmatched()
    accepted = [face for face in faces if face.matched and face.distance is not None]
    if accepted:
        return min(
            accepted,
            key=lambda face: (face.distance if face.distance is not None else math.inf, face.student_id or 0),
        )
    compared = [face for face in faces if face.distance is not None]
    if not compared:
        return _unmatched()
    closest = min(compared, key=lambda face: face.distance if face.distance is not None else math.inf)
    return _unmatched(closest.distance)


def recognize_image(
    image: bytes | np.ndarray,
    gallery: EmbeddingIndex | Sequence[EnrolledEmbedding],
    *,
    threshold: float,
    detect: Detector | None = None,
    encode: Encoder | None = None,
) -> RecognitionResult:
    """Detect, encode, compare, and match every face in one frame.

    The returned object is the best match. `faces` has one entry per
    detected face, in detection order. A frame with no face is unmatched
    and has an empty face list. This function does not mark attendance.
    """
    _require_threshold(threshold)
    index = gallery if isinstance(gallery, EmbeddingIndex) else EmbeddingIndex.build(gallery)
    rgb = frame_to_rgb(image)
    boxes = detect_faces_in_frame(rgb, detect=detect)
    encoded = encode_detected_faces(rgb, boxes, encode=encode) if boxes else []
    faces = tuple(_match_encoded_face(face, index, threshold=threshold) for face in encoded)
    result = RecognitionResult.from_faces(faces)
    logger.info(
        "Recognition result: faces=%d matched=%s student_id=%s distance=%s confidence=%s",
        len(result.faces),
        result.matched,
        result.student_id,
        result.distance,
        result.confidence,
    )
    return result


def _match_encoded_face(
    face: EncodedFace,
    gallery: EmbeddingIndex,
    *,
    threshold: float,
) -> FaceMatch:
    if face.embedding is None:
        return _unmatched()
    return match_embedding(face.embedding, gallery, threshold=threshold)


def _unmatched(distance: float | None = None) -> FaceMatch:
    if distance is None:
        return FaceMatch(student_id=None, matched=False, confidence=0.0, distance=None)
    rounded, confidence = _distance_and_confidence(distance)
    return FaceMatch(student_id=None, matched=False, confidence=confidence, distance=rounded)


def _distance_and_confidence(distance: float) -> tuple[float, float]:
    rounded = round(float(distance), 6)
    if rounded < 0:
        rounded = 0.0
    confidence = round(min(1.0, max(0.0, 1.0 - rounded)), 6)
    return rounded, confidence


def _require_threshold(threshold: float) -> None:
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise ValueError("threshold must be a finite number greater than or equal to 0")
    if not math.isfinite(float(threshold)) or float(threshold) < 0:
        raise ValueError("threshold must be a finite number greater than or equal to 0")
