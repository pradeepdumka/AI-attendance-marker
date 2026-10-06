"""Enroll a student's face and read that enrollment.

Admins and teachers upload one captured frame. The image is checked and
discarded. Only the embedding is stored. Students cannot call these
routes. Marking the recognized student is `POST /attendance/mark`.
"""

from typing import Annotated, NoReturn

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Path,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from app.ai.faces import FaceCaptureError, FaceRecognitionUnavailable, prepare_embedding
from app.auth.dependencies import StaffUser
from app.database.connection import get_db
from app.schemas.face import FaceEnrollmentResponse, to_face_enrollment
from app.services.face_enrollment import (
    FaceCapture,
    FaceEnrollmentError,
    FaceEnrollmentNotAllowed,
    StudentNotActive,
    StudentNotFound,
    TeacherProfileNotFound,
    TooManyFaceSamples,
    enroll_face as enroll_face_record,
    get_face_enrollment as get_face_enrollment_record,
    replace_face_enrollment as replace_face_enrollment_record,
)

router = APIRouter(prefix="/students", tags=["face-enrollment"])

DbSession = Annotated[Session, Depends(get_db)]
StudentId = Annotated[int, Path(ge=1, description="Student profile id")]

MAX_IMAGE_BYTES = 5 * 1024 * 1024

_CAPTURE_RESPONSES = {
    401: {"description": "Missing, expired, or invalid access token"},
    403: {"description": "Signed in as a student"},
    404: {"description": "Student not found, or this teacher is not assigned to the student"},
    409: {"description": "Student is inactive, or the sample limit has been reached"},
    422: {"description": "Missing image, unreadable image, face count, or image quality"},
    503: {"description": "Face recognition library is not installed"},
}


def get_face_capture() -> FaceCapture:
    """Return the capture pipeline. Tests replace this dependency."""
    return prepare_embedding


FaceCaptureDep = Annotated[FaceCapture, Depends(get_face_capture)]


@router.post(
    "/{student_id}/face",
    response_model=FaceEnrollmentResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_CAPTURE_RESPONSES,
)
def enroll_face(
    student_id: StudentId,
    request: Request,
    response: Response,
    current_user: StaffUser,
    db: DbSession,
    capture: FaceCaptureDep,
    image: Annotated[UploadFile, File(description="One captured face image")],
) -> FaceEnrollmentResponse:
    """Add one face sample for a student. Earlier samples stay active."""
    samples = _store(
        enroll_face_record,
        student_id=student_id,
        request=request,
        current_user=current_user,
        db=db,
        capture=capture,
        image=image,
    )
    response.headers["Location"] = f"/students/{student_id}/face"
    return to_face_enrollment(student_id, samples)


@router.put(
    "/{student_id}/face",
    response_model=FaceEnrollmentResponse,
    responses=_CAPTURE_RESPONSES,
)
def replace_face(
    student_id: StudentId,
    request: Request,
    current_user: StaffUser,
    db: DbSession,
    capture: FaceCaptureDep,
    image: Annotated[UploadFile, File(description="One captured face image")],
) -> FaceEnrollmentResponse:
    """Replace the student's face samples with this image."""
    samples = _store(
        replace_face_enrollment_record,
        student_id=student_id,
        request=request,
        current_user=current_user,
        db=db,
        capture=capture,
        image=image,
    )
    return to_face_enrollment(student_id, samples)


@router.get(
    "/{student_id}/face",
    response_model=FaceEnrollmentResponse,
    responses={
        401: {"description": "Missing, expired, or invalid access token"},
        403: {"description": "Signed in as a student"},
        404: {"description": "Student not found, or this teacher is not assigned to the student"},
    },
)
def read_face_enrollment(
    student_id: StudentId,
    current_user: StaffUser,
    db: DbSession,
) -> FaceEnrollmentResponse:
    """Return whether the student has active face samples."""
    try:
        enrolled_student_id, samples = get_face_enrollment_record(
            db,
            actor=current_user,
            student_id=student_id,
        )
    except FaceEnrollmentError as exc:
        _reject(exc)
    return to_face_enrollment(enrolled_student_id, samples)


def _store(
    action,
    *,
    student_id: int,
    request: Request,
    current_user,
    db: Session,
    capture: FaceCapture,
    image: UploadFile,
):
    image_bytes = _read_upload(image)
    try:
        return action(
            db,
            actor=current_user,
            student_id=student_id,
            image_bytes=image_bytes,
            ip_address=_client_ip(request),
            capture=capture,
        )
    except (FaceEnrollmentError, FaceCaptureError) as exc:
        _reject(exc)


def _read_upload(upload: UploadFile) -> bytes:
    content_type = (upload.content_type or "").split(";", 1)[0].strip().lower()
    if content_type and not _image_content_type(content_type):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Upload an image file",
        )
    try:
        data = upload.file.read(MAX_IMAGE_BYTES + 1)
    finally:
        upload.file.close()
    if not data:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Image file is empty",
        )
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Image file is too large",
        )
    return data


def _image_content_type(content_type: str) -> bool:
    return content_type == "application/octet-stream" or content_type.startswith("image/")


def _client_ip(request: Request) -> str | None:
    if request.client is None:
        return None
    host = request.client.host
    return host[:45] if host else None


def _reject(exc: FaceEnrollmentError | FaceCaptureError) -> NoReturn:
    if isinstance(exc, StudentNotFound):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found") from None
    if isinstance(exc, TeacherProfileNotFound):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Teacher profile not found",
        ) from None
    if isinstance(exc, StudentNotActive):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Student is not active",
        ) from None
    if isinstance(exc, TooManyFaceSamples):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.detail) from None
    if isinstance(exc, FaceEnrollmentNotAllowed):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or teacher access required",
        ) from None
    if isinstance(exc, FaceRecognitionUnavailable):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=exc.detail,
        ) from None
    if isinstance(exc, FaceCaptureError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=exc.detail,
        ) from None
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Face enrollment failed",
    ) from exc
