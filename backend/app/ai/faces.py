"""Turn one captured frame into a face embedding.

The enrollment pipeline is: decode the uploaded frame, find faces, require
exactly one, check that face for size, brightness, and blur, then ask the
face_recognition library for a 128-number embedding. The frame stays in
memory. This module does not write an image file and does not mark
attendance. Comparing a frame with enrolled students lives in
`app.ai.recognition`.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import cv2
import numpy as np

# A face_recognition embedding is 128 float64 values. Six decimal places
# are enough for later distance checks and keep the JSON row small.
EMBEDDING_SIZE = 128
EMBEDDING_DECIMALS = 6

MIN_FACE_SIZE = 80
MIN_BRIGHTNESS = 40.0
MAX_BRIGHTNESS = 220.0
MIN_SHARPNESS = 80.0
MAX_IMAGE_SIDE = 1600


class FaceCaptureError(Exception):
    """Expected failure while reading or checking a face image."""

    detail = "Face image was rejected"


class ImageUnreadable(FaceCaptureError):
    """The bytes are empty or not a readable raster image."""

    detail = "Image could not be read"


class NoFaceDetected(FaceCaptureError):
    """The frame does not contain a face."""

    detail = "No face detected"


class MultipleFacesDetected(FaceCaptureError):
    """The frame contains more than one face."""

    detail = "Image must contain exactly one face"


class FaceTooSmall(FaceCaptureError):
    """The only face is below the minimum size."""

    detail = "Face is too small"


class ImageTooDark(FaceCaptureError):
    """The face is too dark to enroll."""

    detail = "Image is too dark"


class ImageTooBright(FaceCaptureError):
    """The face is washed out."""

    detail = "Image is too bright"


class ImageTooBlurry(FaceCaptureError):
    """The face is not sharp enough to enroll."""

    detail = "Image is too blurry"


class EmbeddingFailed(FaceCaptureError):
    """The face was found, but no usable embedding was produced."""

    detail = "Could not generate a face embedding"


class FaceRecognitionUnavailable(FaceCaptureError):
    """The face_recognition library is not installed."""

    detail = "Face recognition is not available"


@dataclass(frozen=True, slots=True)
class FaceBox:
    """One face rectangle in pixel coordinates, top-left origin."""

    top: int
    right: int
    bottom: int
    left: int


Detector = Callable[[np.ndarray], Sequence[FaceBox]]
Encoder = Callable[[np.ndarray, FaceBox], Sequence[float]]


def prepare_embedding(
    image_bytes: bytes,
    *,
    detect: Detector | None = None,
    encode: Encoder | None = None,
) -> list[float]:
    """Run capture, single-face checks, quality checks, and embedding.

    `detect` and `encode` default to the face_recognition library. Tests
    pass stand-ins. The returned list is what gets stored. The original
    bytes are not retained.
    """
    frame = _limit_resolution(capture_image(image_bytes))
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    box = require_single_face((detect or detect_faces)(rgb))
    validate_face_quality(frame, box)
    return compact_embedding((encode or encode_face)(rgb, box))


def frame_to_rgb(image: bytes | np.ndarray) -> np.ndarray:
    """Return an RGB frame ready for face detection.

    Bytes are decoded in memory. An array is treated as an OpenCV BGR
    frame, or as a single-channel frame. The long side is limited to
    `MAX_IMAGE_SIDE`. Nothing is written to disk.
    """
    if isinstance(image, np.ndarray):
        if image.size == 0:
            raise ImageUnreadable
        frame = _limit_resolution(_as_bgr(image))
    elif isinstance(image, (bytes, bytearray)):
        frame = _limit_resolution(capture_image(bytes(image)))
    else:
        raise TypeError("image must be bytes or a numpy array")
    return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)


def capture_image(image_bytes: bytes) -> np.ndarray:
    """Decode an uploaded camera frame into a BGR image.

    Nothing is written to disk. An empty or undecodable payload is rejected
    before face detection runs.
    """
    if not image_bytes:
        raise ImageUnreadable
    array = np.frombuffer(image_bytes, dtype=np.uint8)
    decoded = cv2.imdecode(array, cv2.IMREAD_UNCHANGED)
    if decoded is None or decoded.size == 0:
        raise ImageUnreadable
    return _as_bgr(decoded)


def detect_faces(image_rgb: np.ndarray) -> list[FaceBox]:
    """Find faces with dlib, or OpenCV when dlib is not installed."""
    try:
        locations = _face_recognition().face_locations(image_rgb, model="hog")
    except FaceRecognitionUnavailable:
        return _opencv_face_boxes(image_rgb)
    return [
        FaceBox(top=int(top), right=int(right), bottom=int(bottom), left=int(left))
        for top, right, bottom, left in locations
    ]


def require_single_face(boxes: Sequence[FaceBox]) -> FaceBox:
    """Accept a frame only when it contains one face."""
    count = len(boxes)
    if count == 0:
        raise NoFaceDetected
    if count > 1:
        raise MultipleFacesDetected
    return boxes[0]


def validate_face_quality(image: np.ndarray, box: FaceBox) -> None:
    """Reject a face that is too small, too dark, too bright, or too blurry."""
    crop = _crop(image, box)
    if crop.size == 0 or crop.shape[0] < MIN_FACE_SIZE or crop.shape[1] < MIN_FACE_SIZE:
        raise FaceTooSmall
    gray = _gray(crop)
    brightness = float(gray.mean())
    if brightness < MIN_BRIGHTNESS:
        raise ImageTooDark
    if brightness > MAX_BRIGHTNESS:
        raise ImageTooBright
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if sharpness < MIN_SHARPNESS:
        raise ImageTooBlurry


def encode_face(image_rgb: np.ndarray, box: FaceBox) -> list[float]:
    """Build one compact embedding for a face that already passed validation."""
    try:
        encodings = _face_recognition().face_encodings(
            image_rgb,
            known_face_locations=[(box.top, box.right, box.bottom, box.left)],
            num_jitters=1,
        )
        if len(encodings) != 1:
            raise EmbeddingFailed
        values = encodings[0]
    except FaceRecognitionUnavailable:
        values = _opencv_embedding(image_rgb, box)
    if hasattr(values, "tolist"):
        values = values.tolist()
    return compact_embedding(values)


def compact_embedding(values: Sequence[float]) -> list[float]:
    """Return a fixed-length embedding of rounded finite floats."""
    if len(values) != EMBEDDING_SIZE:
        raise EmbeddingFailed
    compacted: list[float] = []
    for value in values:
        number = float(value)
        if not math.isfinite(number):
            raise EmbeddingFailed
        compacted.append(round(number, EMBEDDING_DECIMALS))
    return compacted


def _face_recognition():
    try:
        import face_recognition
    except ImportError as exc:
        raise FaceRecognitionUnavailable from exc
    return face_recognition


def _opencv_face_boxes(image_rgb: np.ndarray) -> list[FaceBox]:
    """Detect frontal faces without dlib for local demos and fallback installs."""
    gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    detector = cv2.CascadeClassifier(cascade_path)
    if detector.empty():
        raise FaceRecognitionUnavailable
    faces = detector.detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(MIN_FACE_SIZE, MIN_FACE_SIZE),
    )
    return [
        FaceBox(top=int(y), right=int(x + width), bottom=int(y + height), left=int(x))
        for x, y, width, height in faces
    ]


def _opencv_embedding(image_rgb: np.ndarray, box: FaceBox) -> list[float]:
    """Build a simple 128-value descriptor from an aligned grayscale face crop.

    This is a lightweight fallback for machines where dlib cannot be installed.
    The dlib/face_recognition embedding remains the preferred production path.
    """
    crop = _crop(image_rgb, box)
    if crop.size == 0:
        raise EmbeddingFailed
    gray = _gray(crop)
    normalized = cv2.equalizeHist(gray)
    resized = cv2.resize(normalized, (16, 8), interpolation=cv2.INTER_AREA).astype(np.float64)
    vector = resized.reshape(-1) / 255.0
    vector -= float(vector.mean())
    norm = float(np.linalg.norm(vector))
    if norm == 0.0 or not math.isfinite(norm):
        raise EmbeddingFailed
    return compact_embedding((vector / norm).tolist())


def _limit_resolution(image: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    longest = max(height, width)
    if longest <= MAX_IMAGE_SIDE:
        return image
    scale = MAX_IMAGE_SIDE / longest
    return cv2.resize(
        image,
        (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
        interpolation=cv2.INTER_AREA,
    )


def _as_bgr(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    channels = image.shape[2]
    if channels == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    if channels == 3:
        return image
    raise ImageUnreadable


def _crop(image: np.ndarray, box: FaceBox) -> np.ndarray:
    height, width = image.shape[:2]
    top = min(max(box.top, 0), height)
    bottom = min(max(box.bottom, 0), height)
    left = min(max(box.left, 0), width)
    right = min(max(box.right, 0), width)
    return image[top:bottom, left:right]


def _gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
