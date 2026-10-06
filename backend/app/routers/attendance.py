"""Mark attendance and read today's rows, a class roll, and a student history.

A camera frame marks the recognized student once for that subject today.
Staff can also record PRESENT, ABSENT, LATE, or EXCUSED for a known student.
Students can read their own history. They cannot mark the class.
"""

from datetime import date
from typing import Annotated, NoReturn

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from app.ai.faces import FaceCaptureError, FaceRecognitionUnavailable
from app.auth.dependencies import StaffUser, StudentUser
from app.database.connection import get_db
from app.schemas.attendance import (
    AttendanceListResponse,
    AttendanceMarkResponse,
    ManualAttendanceRequest,
    to_attendance,
    to_attendance_mark,
)
from app.services.attendance import (
    AttendanceError,
    AttendanceNotAllowed,
    ClassNotActive,
    ClassNotFound,
    InvalidDateRange,
    StudentNotActive,
    StudentNotFound,
    StudentNotInClass,
    StudentNotRecognized,
    SubjectNotActive,
    SubjectNotFound,
    TeacherNotIdentified,
    TeacherProfileNotFound,
    list_class_attendance,
    list_student_history,
    list_today,
    Recognizer,
    mark_from_frame,
    mark_manual,
)
from app.services.face_recognition import recognize_frame
from app.services.students import get_student_for_user

router = APIRouter(tags=["attendance"])

DbSession = Annotated[Session, Depends(get_db)]
ClassId = Annotated[int, Path(ge=1, description="Class id")]
StudentId = Annotated[int, Path(ge=1, description="Student profile id")]
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]

MAX_IMAGE_BYTES = 5 * 1024 * 1024


def get_recognizer() -> Recognizer:
    """Return the face matcher. Tests replace this dependency."""
    return recognize_frame


def _staff_responses() -> dict[int, dict[str, str]]:
    return {
        401: {"description": "Missing, expired, or invalid access token"},
        403: {"description": "Signed in as a student"},
        404: {"description": "Class, subject, teacher, or student not found"},
    }


@router.post(
    "/attendance/mark",
    response_model=AttendanceMarkResponse,
    responses={
        **_staff_responses(),
        409: {"description": "Student, class, or subject is inactive, or the student is not in the class"},
        422: {"description": "Missing image, unreadable image, or no matching student"},
        503: {"description": "Face recognition library is not installed"},
    },
)
def mark_attendance(
    request: Request,
    response: Response,
    current_user: StaffUser,
    db: DbSession,
    recognize: Annotated[Recognizer, Depends(get_recognizer)],
    image: Annotated[UploadFile, File(description="One camera frame")],
    class_id: Annotated[int, Form(ge=1)],
    subject_id: Annotated[int, Form(ge=1)],
) -> AttendanceMarkResponse:
    """Recognize the frame and mark that student once for this subject today."""
    image_bytes = _read_upload(image)
    try:
        outcome = mark_from_frame(
            db,
            actor=current_user,
            class_id=class_id,
            subject_id=subject_id,
            image_bytes=image_bytes,
            ip_address=_client_ip(request),
            recognize=recognize,
        )
    except (AttendanceError, FaceCaptureError, FaceRecognitionUnavailable) as exc:
        _reject(exc)
    if outcome.created:
        response.status_code = status.HTTP_201_CREATED
        response.headers["Location"] = (
            f"/classes/{class_id}/attendance?subject_id={subject_id}"
        )
    return to_attendance_mark(outcome.record, created=outcome.created)


@router.post(
    "/attendance/manual",
    response_model=AttendanceMarkResponse,
    responses={
        **_staff_responses(),
        409: {"description": "Student, class, or subject is inactive, or the student is not in the class"},
        422: {"description": "Invalid fields"},
    },
)
def mark_attendance_manually(
    body: ManualAttendanceRequest,
    request: Request,
    response: Response,
    current_user: StaffUser,
    db: DbSession,
) -> AttendanceMarkResponse:
    """Record PRESENT, ABSENT, LATE, or EXCUSED without a camera frame."""
    try:
        outcome = mark_manual(
            db,
            actor=current_user,
            student_id=body.student_id,
            class_id=body.class_id,
            subject_id=body.subject_id,
            status=body.status,
            ip_address=_client_ip(request),
        )
    except AttendanceError as exc:
        _reject(exc)
    if outcome.created:
        response.status_code = status.HTTP_201_CREATED
    return to_attendance_mark(outcome.record, created=outcome.created)


@router.get(
    "/attendance/today",
    response_model=AttendanceListResponse,
    responses=_staff_responses(),
)
def read_todays_attendance(
    current_user: StaffUser,
    db: DbSession,
    page: Page = 1,
    page_size: PageSize = 20,
    class_id: Annotated[int | None, Query(ge=1)] = None,
    subject_id: Annotated[int | None, Query(ge=1)] = None,
) -> AttendanceListResponse:
    """Return attendance already stored for today's date."""
    try:
        rows, total = list_today(
            db,
            actor=current_user,
            class_id=class_id,
            subject_id=subject_id,
            page=page,
            page_size=page_size,
        )
    except AttendanceError as exc:
        _reject(exc)
    return _page(rows, total, page, page_size)


@router.get(
    "/classes/{class_id}/attendance",
    response_model=AttendanceListResponse,
    responses=_staff_responses(),
)
def read_class_attendance(
    class_id: ClassId,
    current_user: StaffUser,
    db: DbSession,
    page: Page = 1,
    page_size: PageSize = 20,
    subject_id: Annotated[int | None, Query(ge=1)] = None,
    day: Annotated[date | None, Query(alias="date")] = None,
) -> AttendanceListResponse:
    """Return stored attendance for one class. The default date is today."""
    try:
        rows, total = list_class_attendance(
            db,
            actor=current_user,
            class_id=class_id,
            subject_id=subject_id,
            day=day,
            page=page,
            page_size=page_size,
        )
    except AttendanceError as exc:
        _reject(exc)
    return _page(rows, total, page, page_size)


@router.get(
    "/students/{student_id}/attendance",
    response_model=AttendanceListResponse,
    responses=_staff_responses(),
)
def read_student_attendance(
    student_id: StudentId,
    current_user: StaffUser,
    db: DbSession,
    page: Page = 1,
    page_size: PageSize = 20,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    subject_id: Annotated[int | None, Query(ge=1)] = None,
) -> AttendanceListResponse:
    """Return a student's attendance history, newest date first."""
    try:
        rows, total = list_student_history(
            db,
            actor=current_user,
            student_id=student_id,
            date_from=date_from,
            date_to=date_to,
            subject_id=subject_id,
            page=page,
            page_size=page_size,
        )
    except AttendanceError as exc:
        _reject(exc)
    return _page(rows, total, page, page_size)


@router.get(
    "/student/attendance",
    response_model=AttendanceListResponse,
    responses={
        401: {"description": "Missing, expired, or invalid access token"},
        403: {"description": "Signed in as an admin or a teacher"},
        404: {"description": "This account has no student profile"},
        422: {"description": "The date range ends before it starts"},
    },
)
def read_own_attendance(
    current_user: StudentUser,
    db: DbSession,
    page: Page = 1,
    page_size: PageSize = 20,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    subject_id: Annotated[int | None, Query(ge=1)] = None,
) -> AttendanceListResponse:
    """Return the signed-in student's attendance history."""
    student = get_student_for_user(db, current_user.id)
    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Student profile not found",
        )
    try:
        rows, total = list_student_history(
            db,
            actor=current_user,
            student_id=student.id,
            date_from=date_from,
            date_to=date_to,
            subject_id=subject_id,
            page=page,
            page_size=page_size,
        )
    except AttendanceError as exc:
        _reject(exc)
    return _page(rows, total, page, page_size)


def _page(rows, total: int, page: int, page_size: int) -> AttendanceListResponse:
    return AttendanceListResponse(
        items=[to_attendance(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


def _read_upload(upload: UploadFile) -> bytes:
    content_type = (upload.content_type or "").split(";", 1)[0].strip().lower()
    if content_type and not (
        content_type == "application/octet-stream" or content_type.startswith("image/")
    ):
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


def _client_ip(request: Request) -> str | None:
    if request.client is None:
        return None
    host = request.client.host
    return host[:45] if host else None


def _reject(exc: AttendanceError | FaceCaptureError | FaceRecognitionUnavailable) -> NoReturn:
    if isinstance(exc, (ClassNotFound, SubjectNotFound, StudentNotFound)):
        detail = {
            ClassNotFound: "Class not found",
            SubjectNotFound: "Subject not found",
            StudentNotFound: "Student not found",
        }[type(exc)]
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail) from None
    if isinstance(exc, TeacherProfileNotFound):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Teacher profile not found",
        ) from None
    if isinstance(exc, StudentNotActive):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Student is not active") from None
    if isinstance(exc, StudentNotInClass):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Student is not enrolled in this class",
        ) from None
    if isinstance(exc, ClassNotActive):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Class is not active") from None
    if isinstance(exc, SubjectNotActive):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Subject is not active",
        ) from None
    if isinstance(exc, TeacherNotIdentified):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Subject has no teacher",
        ) from None
    if isinstance(exc, StudentNotRecognized):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="No matching student",
        ) from None
    if isinstance(exc, InvalidDateRange):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="date_from must be on or before date_to",
        ) from None
    if isinstance(exc, AttendanceNotAllowed):
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
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Attendance failed") from exc
