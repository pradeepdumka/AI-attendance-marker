"""Face recognition. These tests use stand-in embeddings and do not need dlib, a camera, or MySQL."""

import logging

import cv2
import numpy as np
import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai.faces import EMBEDDING_SIZE, EmbeddingFailed, FaceBox, ImageUnreadable
from app.ai.recognition import (
    EmbeddingIndex,
    EnrolledEmbedding,
    best_match,
    compare_embedding,
    detect_faces_in_frame,
    encode_detected_faces,
    match_embedding,
    recognize_image,
)
from app.config import get_settings
from app.models import Attendance, Base, FaceEncoding, Student, User, UserRole
from app.services.face_recognition import (
    EmbeddingCache,
    load_active_embeddings,
    recognize_frame,
)

_OUTPUT_FIELDS = ("student_id", "matched", "confidence", "distance")


def _vector(first: float) -> list[float]:
    values = [0.0] * EMBEDDING_SIZE
    values[0] = first
    return values


def _enrolled(student_id: int, first: float) -> EnrolledEmbedding:
    return EnrolledEmbedding(student_id=student_id, embedding=tuple(_vector(first)))


def _png() -> bytes:
    image = np.full((32, 32, 3), 128, dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


def _one_face(_image: np.ndarray) -> list[FaceBox]:
    return [FaceBox(top=0, right=8, bottom=8, left=0)]


def _faces(boxes: list[FaceBox]):
    def detect(_image: np.ndarray) -> list[FaceBox]:
        return boxes

    return detect


def test_comparison_does_not_detect_or_encode() -> None:
    calls = {"detect": 0, "encode": 0}

    def detect(_image: np.ndarray) -> list[FaceBox]:
        calls["detect"] += 1
        return _one_face(_image)

    def encode(_image: np.ndarray, _box: FaceBox) -> list[float]:
        calls["encode"] += 1
        return _vector(0.25)

    gallery = EmbeddingIndex.build([_enrolled(4, 0.0), _enrolled(9, 1.0)])
    comparison = compare_embedding(_vector(0.25), gallery)
    decision = match_embedding(_vector(0.25), gallery, threshold=0.6)

    assert comparison is not None
    assert comparison.student_id == 4
    assert comparison.distance == pytest.approx(0.25)
    assert decision.as_dict() == {
        "student_id": 4,
        "matched": True,
        "confidence": 0.75,
        "distance": 0.25,
    }
    assert calls == {"detect": 0, "encode": 0}

    result = recognize_image(_png(), gallery, threshold=0.6, detect=detect, encode=encode)
    assert calls == {"detect": 1, "encode": 1}
    assert result.student_id == 4


def test_nearest_sample_wins_and_ties_use_the_smaller_student_id() -> None:
    gallery = EmbeddingIndex.build(
        [
            _enrolled(7, 0.0),
            _enrolled(7, 0.5),
            _enrolled(8, 1.0),
        ]
    )
    closest_sample = compare_embedding(_vector(0.5), gallery)
    assert closest_sample is not None
    assert closest_sample.student_id == 7
    assert closest_sample.distance == pytest.approx(0.0)

    tie = EmbeddingIndex.build([_enrolled(5, 0.0), _enrolled(2, 0.5)])
    chosen = match_embedding(_vector(0.25), tie, threshold=0.6)
    assert chosen.student_id == 2
    assert chosen.distance == pytest.approx(0.25)


def test_distance_above_the_threshold_is_rejected() -> None:
    gallery = EmbeddingIndex.build([_enrolled(3, 0.0)])
    rejected = match_embedding(_vector(0.75), gallery, threshold=0.6)
    accepted = match_embedding(_vector(0.5), gallery, threshold=0.5)

    assert rejected.as_dict() == {
        "student_id": None,
        "matched": False,
        "confidence": 0.25,
        "distance": 0.75,
    }
    assert accepted.matched is True
    assert accepted.student_id == 3
    assert accepted.distance == pytest.approx(0.5)


def test_an_empty_gallery_does_not_match() -> None:
    gallery = EmbeddingIndex.build(())
    assert compare_embedding(_vector(0.0), gallery) is None
    assert match_embedding(_vector(0.0), gallery, threshold=0.6).as_dict() == {
        "student_id": None,
        "matched": False,
        "confidence": 0.0,
        "distance": None,
    }


def test_best_match_uses_the_closest_accepted_face() -> None:
    faces = [
        match_embedding(_vector(0.5), EmbeddingIndex.build([_enrolled(1, 0.0)]), threshold=0.6),
        match_embedding(_vector(0.0), EmbeddingIndex.build([_enrolled(2, 0.0)]), threshold=0.6),
        match_embedding(_vector(0.75), EmbeddingIndex.build([_enrolled(3, 0.0)]), threshold=0.6),
    ]
    chosen = best_match(faces)
    assert chosen.student_id == 2
    assert chosen.distance == pytest.approx(0.0)
    assert best_match(()).as_dict() == {
        "student_id": None,
        "matched": False,
        "confidence": 0.0,
        "distance": None,
    }


def test_no_face_is_unmatched_and_does_not_encode() -> None:
    def encode(_image: np.ndarray, _box: FaceBox) -> list[float]:
        raise AssertionError("encoder ran")

    result = recognize_image(
        _png(),
        [_enrolled(1, 0.0)],
        threshold=0.6,
        detect=lambda _image: [],
        encode=encode,
    )

    assert result.faces == ()
    assert result.as_dict() == {
        "student_id": None,
        "matched": False,
        "confidence": 0.0,
        "distance": None,
    }
    assert tuple(result.as_dict()) == _OUTPUT_FIELDS


def test_several_faces_are_matched_and_the_closest_is_returned() -> None:
    encoded: list[float] = []

    def encode(_image: np.ndarray, box: FaceBox) -> list[float]:
        vector = _vector(0.25 if box.left == 0 else 1.0)
        encoded.append(vector[0])
        return vector

    result = recognize_image(
        _png(),
        [_enrolled(1, 0.0), _enrolled(2, 1.0)],
        threshold=0.6,
        detect=_faces(
            [
                FaceBox(top=0, right=8, bottom=8, left=0),
                FaceBox(top=0, right=16, bottom=8, left=8),
            ]
        ),
        encode=encode,
    )

    assert encoded == [0.25, 1.0]
    assert [face.student_id for face in result.faces] == [1, 2]
    assert [face.matched for face in result.faces] == [True, True]
    assert result.as_dict() == {
        "student_id": 2,
        "matched": True,
        "confidence": 1.0,
        "distance": 0.0,
    }


def test_a_rejected_face_does_not_hide_another_match() -> None:
    def encode(_image: np.ndarray, box: FaceBox) -> list[float]:
        return _vector(0.75 if box.left == 0 else 0.25)

    result = recognize_image(
        np.full((16, 16, 3), 100, dtype=np.uint8),
        [_enrolled(1, 0.0)],
        threshold=0.6,
        detect=_faces(
            [
                FaceBox(top=0, right=8, bottom=8, left=0),
                FaceBox(top=0, right=16, bottom=8, left=8),
            ]
        ),
        encode=encode,
    )

    assert result.faces[0].matched is False
    assert result.faces[0].student_id is None
    assert result.faces[0].distance == pytest.approx(0.75)
    assert result.faces[1].as_dict() == {
        "student_id": 1,
        "matched": True,
        "confidence": 0.75,
        "distance": 0.25,
    }
    assert result.student_id == 1


def test_one_encoding_failure_leaves_the_other_face(caplog: pytest.LogCaptureFixture) -> None:
    def encode(_image: np.ndarray, box: FaceBox) -> list[float]:
        if box.left == 0:
            raise EmbeddingFailed
        return _vector(0.25)

    caplog.set_level(logging.WARNING, logger="app.ai.recognition")
    boxes = [
        FaceBox(top=1, right=9, bottom=9, left=0),
        FaceBox(top=1, right=18, bottom=9, left=9),
    ]
    image = np.zeros((20, 20, 3), dtype=np.uint8)
    encoded = encode_detected_faces(image, boxes, encode=encode)
    result = recognize_image(
        image,
        [_enrolled(6, 0.0)],
        threshold=0.6,
        detect=_faces(boxes),
        encode=encode,
    )

    assert encoded[0].embedding is None
    assert encoded[1].embedding is not None
    assert result.faces[0].matched is False
    assert result.faces[0].distance is None
    assert result.student_id == 6
    assert "Face encoding failed at top=1 right=9 bottom=9 left=0" in caplog.text


def test_detection_returns_every_box_without_encoding() -> None:
    boxes = [FaceBox(0, 4, 4, 0), FaceBox(0, 8, 4, 4)]
    image = np.zeros((8, 8, 3), dtype=np.uint8)
    assert detect_faces_in_frame(image, detect=_faces(boxes)) == boxes


def test_unreadable_bytes_are_rejected_before_matching() -> None:
    def detect(_image: np.ndarray) -> list[FaceBox]:
        raise AssertionError("detector ran")

    with pytest.raises(ImageUnreadable):
        recognize_image(b"not-an-image", [_enrolled(1, 0.0)], threshold=0.6, detect=detect)


def test_a_short_embedding_is_not_compared() -> None:
    gallery = EmbeddingIndex.build([_enrolled(1, 0.0)])
    with pytest.raises(EmbeddingFailed):
        compare_embedding([0.1, 0.2], gallery)
    with pytest.raises(EmbeddingFailed):
        EmbeddingIndex.build([EnrolledEmbedding(student_id=1, embedding=(0.1, 0.2))])


def test_threshold_must_be_a_finite_number() -> None:
    gallery = EmbeddingIndex.build([_enrolled(1, 0.0)])
    with pytest.raises(ValueError):
        match_embedding(_vector(0.0), gallery, threshold=float("nan"))


def test_threshold_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FACE_MATCH_THRESHOLD", "0.2")
    get_settings.cache_clear()

    strict = recognize_frame(
        _png(),
        gallery=[_enrolled(1, 0.0)],
        detect=_one_face,
        encode=lambda _image, _box: _vector(0.25),
    )
    allowed = recognize_frame(
        _png(),
        gallery=[_enrolled(1, 0.0)],
        detect=_one_face,
        encode=lambda _image, _box: _vector(0.125),
    )

    assert strict.as_dict() == {
        "student_id": None,
        "matched": False,
        "confidence": 0.75,
        "distance": 0.25,
    }
    assert allowed.student_id == 1
    assert allowed.matched is True
    assert allowed.distance == pytest.approx(0.125)


def test_a_prepared_gallery_does_not_query_the_database() -> None:
    class ExplodingSession:
        def execute(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("database was queried")

    result = recognize_frame(
        _png(),
        db=ExplodingSession(),  # type: ignore[arg-type]
        gallery=[_enrolled(1, 0.0)],
        detect=_one_face,
        encode=lambda _image, _box: _vector(0.0),
    )

    assert result.matched is True
    assert result.student_id == 1


def test_warm_cache_serves_later_frames_until_it_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FACE_EMBEDDING_CACHE_SECONDS", "60")
    get_settings.cache_clear()
    clock = {"now": 10.0}
    cache = EmbeddingCache(clock=lambda: clock["now"])
    loads = {"n": 0}

    def load(_db: object) -> list[EnrolledEmbedding]:
        loads["n"] += 1
        return [_enrolled(1, 0.0)]

    def run() -> None:
        recognize_frame(
            _png(),
            db=object(),  # type: ignore[arg-type]
            detect=_one_face,
            encode=lambda _image, _box: _vector(0.25),
            load_embeddings=load,  # type: ignore[arg-type]
            cache=cache,
        )

    run()
    clock["now"] = 69.0
    run()
    assert loads["n"] == 1
    clock["now"] = 70.0
    run()
    assert loads["n"] == 2


def test_a_zero_cache_ttl_loads_on_every_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FACE_EMBEDDING_CACHE_SECONDS", "0")
    get_settings.cache_clear()
    loads = {"n": 0}
    cache = EmbeddingCache()

    def load(_db: object) -> list[EnrolledEmbedding]:
        loads["n"] += 1
        return [_enrolled(1, 0.0)]

    for _ in range(2):
        recognize_frame(
            _png(),
            db=object(),  # type: ignore[arg-type]
            detect=_one_face,
            encode=lambda _image, _box: _vector(0.0),
            load_embeddings=load,  # type: ignore[arg-type]
            cache=cache,
        )

    assert loads["n"] == 2


def test_invalidating_the_cache_forces_the_next_load(caplog: pytest.LogCaptureFixture) -> None:
    cache = EmbeddingCache()
    loads = {"n": 0}

    def load(_db: object) -> list[EnrolledEmbedding]:
        loads["n"] += 1
        return [_enrolled(1, 0.0)]

    def run() -> None:
        recognize_frame(
            _png(),
            db=object(),  # type: ignore[arg-type]
            detect=_one_face,
            encode=lambda _image, _box: _vector(0.0),
            load_embeddings=load,  # type: ignore[arg-type]
            cache=cache,
        )

    caplog.set_level(logging.INFO, logger="app.services.face_recognition")
    run()
    run()
    cache.invalidate()
    run()

    assert loads["n"] == 2
    assert "Loaded active face embeddings: samples=1 students=1" in caplog.text
    assert "Cleared cached face embeddings" in caplog.text


def test_recognition_logs_the_best_match_and_a_rejection(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.ai.recognition")
    recognize_image(
        _png(),
        [_enrolled(1, 0.0)],
        threshold=0.6,
        detect=_one_face,
        encode=lambda _image, _box: _vector(0.25),
    )
    recognize_image(
        _png(),
        [_enrolled(1, 0.0)],
        threshold=0.6,
        detect=_one_face,
        encode=lambda _image, _box: _vector(0.75),
    )

    assert "Recognition result: faces=1 matched=True student_id=1 distance=0.25 confidence=0.75" in caplog.text
    assert "Rejected low-confidence face match: closest_student_id=1 distance=0.75 threshold=0.6" in caplog.text
    assert "Recognition result: faces=1 matched=False student_id=None distance=0.75 confidence=0.25" in caplog.text


def test_a_frame_requires_a_gallery_or_a_session() -> None:
    with pytest.raises(ValueError, match="gallery"):
        recognize_frame(_png())


def _sqlite_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    saved_defaults = [
        (column, column.server_default)
        for table in Base.metadata.tables.values()
        for column in table.columns
    ]
    try:
        for column, _default in saved_defaults:
            column.server_default = None
        Base.metadata.create_all(engine)
    finally:
        for column, default in saved_defaults:
            column.server_default = default
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    return engine, session


def _user(session, *, email: str, active: bool) -> User:
    user = User(
        first_name="Ada",
        last_name="Lovelace",
        email=email,
        password_hash="not-used",
        role=UserRole.STUDENT,
        is_active=active,
    )
    session.add(user)
    session.flush()
    return user


def test_active_embeddings_are_loaded_once_and_attendance_is_not_written(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _engine, session = _sqlite_session()
    active = _user(session, email="ada@school.edu", active=True)
    inactive = _user(session, email="grace@school.edu", active=False)
    student = Student(user_id=active.id, roll_number="2026-014")
    other = Student(user_id=inactive.id, roll_number="2026-015")
    session.add_all([student, other])
    session.flush()
    session.add_all(
        [
            FaceEncoding(student_id=student.id, encoding=_vector(0.0), is_active=True),
            FaceEncoding(student_id=student.id, encoding=_vector(0.5), is_active=True),
            FaceEncoding(student_id=student.id, encoding=_vector(1.0), is_active=False),
            FaceEncoding(student_id=other.id, encoding=_vector(0.25), is_active=True),
            FaceEncoding(student_id=student.id, encoding=[0.1, 0.2], is_active=True),
        ]
    )
    session.commit()

    statements: list[str] = []

    @event.listens_for(session.get_bind(), "before_cursor_execute")
    def _capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    caplog.set_level(logging.WARNING, logger="app.services.face_recognition")
    loaded = load_active_embeddings(session)
    assert [(entry.student_id, entry.embedding[0]) for entry in loaded] == [
        (student.id, 0.0),
        (student.id, 0.5),
    ]
    assert "Skipping invalid face embedding" in caplog.text

    def gallery_selects() -> list[str]:
        return [
            statement
            for statement in statements
            if "face_encodings" in statement.lower() and statement.lstrip().lower().startswith("select")
        ]

    assert len(gallery_selects()) == 1
    statements.clear()

    def recognize() -> None:
        result = recognize_frame(
            _png(),
            db=session,
            detect=_one_face,
            encode=lambda _image, _box: _vector(0.5),
        )
        assert result.as_dict() == {
            "student_id": student.id,
            "matched": True,
            "confidence": 1.0,
            "distance": 0.0,
        }

    recognize()
    recognize()

    assert len(gallery_selects()) == 1
    assert session.scalar(select(func.count()).select_from(Attendance)) == 0
    session.close()
