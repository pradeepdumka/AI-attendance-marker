"""Match a camera frame to enrolled students.

Active embeddings are loaded once and reused until the cache expires or a
face sample is saved. A frame does not query MySQL when that cache is
warm. This service does not create attendance records.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.faces import Detector, EmbeddingFailed, Encoder, compact_embedding
from app.ai.recognition import (
    EmbeddingIndex,
    EnrolledEmbedding,
    RecognitionResult,
    recognize_image,
)
from app.config import get_settings
from app.models import FaceEncoding, Student, User

logger = logging.getLogger(__name__)

EmbeddingLoader = Callable[[Session], Sequence[EnrolledEmbedding]]
Clock = Callable[[], float]


class EmbeddingCache:
    """In-memory copy of the active gallery.

    `get` holds its lock across the load so two frames cannot both miss
    and query at the same time. The returned index is not mutated.
    """

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._index = EmbeddingIndex.build(())
        self._loaded_at: float | None = None

    def get(
        self,
        db: Session,
        *,
        ttl_seconds: float,
        load: EmbeddingLoader,
    ) -> EmbeddingIndex:
        """Return the cached gallery, loading it when the TTL has elapsed."""
        with self._lock:
            now = self._clock()
            if self._loaded_at is not None and now - self._loaded_at < ttl_seconds:
                logger.debug("Using %d cached face embeddings", self._index.sample_count)
                return self._index
            entries = tuple(load(db))
            self._index = EmbeddingIndex.build(entries)
            self._loaded_at = now
            logger.info(
                "Loaded active face embeddings: samples=%d students=%d",
                self._index.sample_count,
                self._index.student_count,
            )
            return self._index

    def invalidate(self) -> None:
        """Drop the gallery so the next recognition call reads it again."""
        with self._lock:
            was_loaded = self._loaded_at is not None
            self._index = EmbeddingIndex.build(())
            self._loaded_at = None
            if was_loaded:
                logger.info("Cleared cached face embeddings")


_cache = EmbeddingCache()


def get_embedding_cache() -> EmbeddingCache:
    """Return the process-wide gallery cache."""
    return _cache


def invalidate_embedding_cache() -> None:
    """Forget cached embeddings. Call this after a face sample changes."""
    _cache.invalidate()


def load_active_embeddings(db: Session) -> list[EnrolledEmbedding]:
    """Read every active embedding for an active student in one query.

    Inactive samples and inactive accounts are omitted. A stored value that
    is not a 128-number embedding is skipped and is not logged in full.
    """
    rows = db.execute(
        select(FaceEncoding.student_id, FaceEncoding.encoding)
        .join(Student, Student.id == FaceEncoding.student_id)
        .join(User, User.id == Student.user_id)
        .where(
            FaceEncoding.is_active.is_(True),
            User.is_active.is_(True),
        )
        .order_by(FaceEncoding.student_id, FaceEncoding.id)
    ).all()
    entries: list[EnrolledEmbedding] = []
    for student_id, encoding in rows:
        if not isinstance(encoding, (list, tuple)):
            logger.warning(
                "Skipping face embedding for student %s: stored value is not a list",
                student_id,
            )
            continue
        try:
            vector = compact_embedding(encoding)
        except EmbeddingFailed:
            logger.warning("Skipping invalid face embedding for student %s", student_id)
            continue
        entries.append(EnrolledEmbedding(student_id=int(student_id), embedding=tuple(vector)))
    return entries


def recognize_frame(
    image: bytes | np.ndarray,
    *,
    db: Session | None = None,
    gallery: Sequence[EnrolledEmbedding] | EmbeddingIndex | None = None,
    threshold: float | None = None,
    detect: Detector | None = None,
    encode: Encoder | None = None,
    load_embeddings: EmbeddingLoader | None = None,
    cache: EmbeddingCache | None = None,
) -> RecognitionResult:
    """Recognize the best enrolled student in a frame.

    Pass `gallery` to compare against a prepared set and skip the database.
    Otherwise `db` is used only when the embedding cache is cold. The
    threshold defaults to `FACE_MATCH_THRESHOLD`. The result does not
    create an attendance row.

    The return value's `as_dict` is `student_id`, `matched`, `confidence`,
    and `distance` for the best face. `faces` lists every detected face.
    """
    settings = get_settings()
    limit = settings.face_match_threshold if threshold is None else threshold
    if gallery is not None:
        index: EmbeddingIndex | Sequence[EnrolledEmbedding] = gallery
    else:
        if db is None:
            raise ValueError("A database session or a gallery is required")
        index = (cache or get_embedding_cache()).get(
            db,
            ttl_seconds=settings.face_embedding_cache_seconds,
            load=load_embeddings or load_active_embeddings,
        )
    return recognize_image(
        image,
        index,
        threshold=limit,
        detect=detect,
        encode=encode,
    )
