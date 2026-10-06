"""Face count and image-quality checks. These tests do not need a camera or dlib."""

import sys
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from app.ai.faces import (
    EMBEDDING_SIZE,
    MAX_BRIGHTNESS,
    MAX_IMAGE_SIDE,
    MIN_BRIGHTNESS,
    MIN_FACE_SIZE,
    MIN_SHARPNESS,
    EmbeddingFailed,
    FaceBox,
    FaceRecognitionUnavailable,
    FaceTooSmall,
    ImageTooBlurry,
    ImageTooBright,
    ImageTooDark,
    ImageUnreadable,
    MultipleFacesDetected,
    NoFaceDetected,
    compact_embedding,
    detect_faces,
    encode_face,
    prepare_embedding,
    require_single_face,
    validate_face_quality,
)


def _checkerboard(size: int, low: int, high: int) -> np.ndarray:
    image = np.empty((size, size, 3), dtype=np.uint8)
    ys, xs = np.indices((size, size))
    dark = (ys + xs) % 2 == 0
    image[dark] = low
    image[~dark] = high
    return image


def _png(image: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


def _full_box(image: np.ndarray) -> FaceBox:
    height, width = image.shape[:2]
    return FaceBox(top=0, right=width, bottom=height, left=0)


def _embedding(image: np.ndarray, box: FaceBox) -> list[float]:
    return [0.25] * EMBEDDING_SIZE


def test_zero_faces_are_rejected() -> None:
    with pytest.raises(NoFaceDetected) as caught:
        require_single_face([])
    assert caught.value.detail == "No face detected"

    image = _checkerboard(MIN_FACE_SIZE, 80, 160)
    with pytest.raises(NoFaceDetected):
        prepare_embedding(_png(image), detect=lambda _image: [], encode=_embedding)


def test_multiple_faces_are_rejected_before_encoding() -> None:
    box = FaceBox(0, MIN_FACE_SIZE, MIN_FACE_SIZE, 0)
    with pytest.raises(MultipleFacesDetected) as caught:
        require_single_face([box, box])
    assert caught.value.detail == "Image must contain exactly one face"

    def encode(_image: np.ndarray, _box: FaceBox) -> list[float]:
        raise AssertionError("encoder ran")

    image = _checkerboard(MIN_FACE_SIZE, 80, 160)
    with pytest.raises(MultipleFacesDetected):
        prepare_embedding(_png(image), detect=lambda _image: [box, box], encode=encode)


def test_a_face_smaller_than_the_minimum_is_rejected() -> None:
    image = _checkerboard(MIN_FACE_SIZE - 1, 80, 160)
    with pytest.raises(FaceTooSmall) as caught:
        validate_face_quality(image, _full_box(image))
    assert caught.value.detail == "Face is too small"


def test_dark_bright_and_blurry_faces_are_rejected() -> None:
    size = MIN_FACE_SIZE
    dark = np.full((size, size, 3), int(MIN_BRIGHTNESS) - 1, dtype=np.uint8)
    bright = np.full((size, size, 3), int(MAX_BRIGHTNESS) + 1, dtype=np.uint8)
    blurry = np.full((size, size, 3), 128, dtype=np.uint8)

    with pytest.raises(ImageTooDark):
        validate_face_quality(dark, _full_box(dark))
    with pytest.raises(ImageTooBright):
        validate_face_quality(bright, _full_box(bright))
    with pytest.raises(ImageTooBlurry) as caught:
        validate_face_quality(blurry, _full_box(blurry))
    assert caught.value.detail == "Image is too blurry"
    assert float(cv2.Laplacian(blurry, cv2.CV_64F).var()) < MIN_SHARPNESS


def test_a_sharp_single_face_at_the_quality_boundaries_is_accepted() -> None:
    size = MIN_FACE_SIZE
    dark_enough = _checkerboard(size, 20, 60)
    bright_enough = _checkerboard(size, 200, 240)
    assert float(dark_enough.mean()) == MIN_BRIGHTNESS
    assert float(bright_enough.mean()) == MAX_BRIGHTNESS

    validate_face_quality(dark_enough, _full_box(dark_enough))
    validate_face_quality(bright_enough, _full_box(bright_enough))

    too_dark = _checkerboard(size, 19, 59)
    too_bright = _checkerboard(size, 201, 241)
    with pytest.raises(ImageTooDark):
        validate_face_quality(too_dark, _full_box(too_dark))
    with pytest.raises(ImageTooBright):
        validate_face_quality(too_bright, _full_box(too_bright))


def test_prepare_embedding_returns_a_compact_vector_and_writes_no_image(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    image = _checkerboard(96, 80, 160)
    box = _full_box(image)

    embedding = prepare_embedding(_png(image), detect=lambda _image: [box], encode=_embedding)

    assert embedding == [0.25] * EMBEDDING_SIZE
    assert list(tmp_path.iterdir()) == []


def test_unreadable_and_empty_images_are_rejected() -> None:
    with pytest.raises(ImageUnreadable) as caught:
        prepare_embedding(b"not-an-image")
    assert caught.value.detail == "Image could not be read"
    with pytest.raises(ImageUnreadable):
        prepare_embedding(b"")


def test_a_large_frame_is_scaled_before_detection() -> None:
    height, width = 100, MAX_IMAGE_SIDE + 400
    image = np.empty((height, width, 3), dtype=np.uint8)
    ys, xs = np.indices((height, width))
    dark = ((ys // 10) + (xs // 10)) % 2 == 0
    image[dark] = 80
    image[~dark] = 160
    seen: dict[str, tuple[int, ...]] = {}

    def detect(rgb: np.ndarray) -> list[FaceBox]:
        seen["shape"] = rgb.shape
        return [_full_box(rgb)]

    embedding = prepare_embedding(_png(image), detect=detect, encode=_embedding)

    assert seen["shape"][0] <= MAX_IMAGE_SIDE
    assert seen["shape"][1] <= MAX_IMAGE_SIDE
    assert len(embedding) == EMBEDDING_SIZE


def test_embedding_must_be_128_finite_numbers() -> None:
    with pytest.raises(EmbeddingFailed):
        compact_embedding([0.1] * (EMBEDDING_SIZE - 1))
    with pytest.raises(EmbeddingFailed):
        compact_embedding([float("nan")] * EMBEDDING_SIZE)
    with pytest.raises(EmbeddingFailed):
        compact_embedding([float("inf")] * EMBEDDING_SIZE)

    stored = compact_embedding([1 / 3] * EMBEDDING_SIZE)
    assert len(stored) == EMBEDDING_SIZE
    assert stored[0] == round(1 / 3, 6)


def test_default_detector_and_encoder_use_face_recognition(monkeypatch) -> None:
    calls: dict[str, object] = {}

    def face_locations(image: np.ndarray, model: str = "hog"):
        calls["model"] = model
        calls["detect_shape"] = image.shape
        return [(4, 40, 50, 6)]

    def face_encodings(image: np.ndarray, known_face_locations, num_jitters: int = 1):
        calls["locations"] = known_face_locations
        calls["jitters"] = num_jitters
        return [np.linspace(0, 1, EMBEDDING_SIZE)]

    monkeypatch.setitem(
        sys.modules,
        "face_recognition",
        SimpleNamespace(face_locations=face_locations, face_encodings=face_encodings),
    )

    boxes = detect_faces(np.zeros((60, 60, 3), dtype=np.uint8))
    embedding = encode_face(np.zeros((60, 60, 3), dtype=np.uint8), boxes[0])

    assert boxes == [FaceBox(top=4, right=40, bottom=50, left=6)]
    assert calls["model"] == "hog"
    assert calls["locations"] == [(4, 40, 50, 6)]
    assert calls["jitters"] == 1
    assert len(embedding) == EMBEDDING_SIZE
    assert embedding[0] == 0.0


def test_a_missing_face_recognition_library_is_reported(monkeypatch) -> None:
    monkeypatch.delitem(sys.modules, "face_recognition", raising=False)
    real_import = __import__

    def blocked(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "face_recognition":
            raise ImportError("blocked")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", blocked)
    with pytest.raises(FaceRecognitionUnavailable) as caught:
        detect_faces(np.zeros((8, 8, 3), dtype=np.uint8))
    assert caught.value.detail == "Face recognition is not available"


def test_encoder_failure_is_rejected(monkeypatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "face_recognition",
        SimpleNamespace(face_encodings=lambda *_args, **_kwargs: []),
    )
    with pytest.raises(EmbeddingFailed):
        encode_face(np.zeros((8, 8, 3), dtype=np.uint8), FaceBox(0, 4, 4, 0))
