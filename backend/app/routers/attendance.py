"""Mark attendance and read today's rows, a class roll, and a student history.

A camera frame marks the recognized student once for that subject today.
Staff can also record PRESENT, ABSENT, LATE, or EXCUSED for a known student.
Students can read their own history, percentage, month, and calendar.
They cannot mark attendance or read another student's rows.

A teacher reads and exports only assigned classes and subjects, and opens
an attendance session before taking a lesson from the teacher dashboard.
"""

import csv
import io
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
from app.auth.dependencies import StaffUser, StudentUser, TeacherUser
from app.database.connection import get_db
from app.models.user import User
from app.schemas.attendance import (
    AttendanceCalendarResponse,
    AttendanceListResponse,
    AttendanceMarkResponse,
    AttendanceMonthResponse,
    AttendancePercentageResponse,
    AttendanceSessionResponse,
    AttendanceSessionStartRequest,
    AttendanceStatisticsResponse,
    ManualAttendanceRequest,
    SubjectAttendanceListResponse,
    to_attendance,
    to_attendance_mark,
    to_attendance_session,
    to_calendar_day,
    to_month,
    to_percentage,
    to_statistics,
    to_subject_attendance,
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
    SessionNotFound,
    StudentNotRecognized,
    SubjectNotActive,
    SubjectNotFound,
    TeacherNotIdentified,
    TeacherProfileNotFound,
    attendance_statistics,
    close_attendance_session,
    export_attendance,
    list_class_attendance,
    list_history,
    list_student_history,
    list_today,
    local_now,
    Recognizer,
    mark_from_frame,
    mark_manual,
    start_attendance_session,
    student_attendance_by_subject,
    student_attendance_calendar,
    student_attendance_counts,
    student_month_counts,
)
from app.services.face_recognition import recognize_frame
from app.services.students import get_student_for_user

router = APIRouter(tags=["attendance"])

DbSession = Annotated[Session, Depends(get_db)]
ClassId = Annotated[int, Path(ge=1, description="Class id")]
StudentId = Annotated[int, Path(ge=1, description="Student profile id")]
SessionId = Annotated[int, Path(ge=1, description="Attendance session id")]
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


def _student_read_responses() -> dict[int, dict[str, str]]:
    return {
        401: {"description": "Missing, expired, or invalid access token"},
        403: {"description": "Signed in as an admin or a teacher"},
        404: {"description": "This account has no student profile"},
        422: {"description": "The date range ends before it starts"},
    }


def _own_student(current_user: User, db: Session) -> int:
    student = get_student_for_user(db, current_user.id)
    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Student profile not found",
        )
    return student.id


def _selected_month(year: int | None, month: int | None) -> tuple[int, int]:
    if year is None and month is None:
        today = local_now().date()
        return today.year, today.month
    if year is None or month is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="year and month are required together",
        )
    return year, month


@router.get(
    "/student/attendance/percentage",
    response_model=AttendancePercentageResponse,
    responses=_student_read_responses(),
)
def read_own_percentage(
    current_user: StudentUser,
    db: DbSession,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    subject_id: Annotated[int | None, Query(ge=1)] = None,
) -> AttendancePercentageResponse:
    """Return the signed-in student's attendance percentage."""
    student_id = _own_student(current_user, db)
    try:
        counts = student_attendance_counts(
            db,
            actor=current_user,
            student_id=student_id,
            date_from=date_from,
            date_to=date_to,
            subject_id=subject_id,
        )
    except AttendanceError as exc:
        _reject(exc)
    return to_percentage(counts)


@router.get(
    "/student/attendance/by-subject",
    response_model=SubjectAttendanceListResponse,
    responses=_student_read_responses(),
)
def read_own_attendance_by_subject(
    current_user: StudentUser,
    db: DbSession,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
) -> SubjectAttendanceListResponse:
    """Return the signed-in student's attendance for each visible subject."""
    student_id = _own_student(current_user, db)
    try:
        rows = student_attendance_by_subject(
            db,
            actor=current_user,
            student_id=student_id,
            date_from=date_from,
            date_to=date_to,
        )
    except AttendanceError as exc:
        _reject(exc)
    return SubjectAttendanceListResponse(
        items=[
            to_subject_attendance(
                subject_id=subject.id,
                class_id=subject.class_id,
                name=subject.name,
                code=subject.code,
                counts=counts,
            )
            for subject, counts in rows
        ]
    )


@router.get(
    "/student/attendance/monthly",
    response_model=AttendanceMonthResponse,
    responses={
        **_student_read_responses(),
        422: {"description": "Year and month were not sent together, or a value is out of range"},
    },
)
def read_own_month(
    current_user: StudentUser,
    db: DbSession,
    year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
    month: Annotated[int | None, Query(ge=1, le=12)] = None,
    subject_id: Annotated[int | None, Query(ge=1)] = None,
) -> AttendanceMonthResponse:
    """Return the signed-in student's attendance for one calendar month."""
    selected_year, selected_month = _selected_month(year, month)
    student_id = _own_student(current_user, db)
    try:
        counts = student_month_counts(
            db,
            actor=current_user,
            student_id=student_id,
            year=selected_year,
            month=selected_month,
            subject_id=subject_id,
        )
    except AttendanceError as exc:
        _reject(exc)
    return to_month(counts, year=selected_year, month=selected_month)


@router.get(
    "/student/attendance/calendar",
    response_model=AttendanceCalendarResponse,
    responses={
        **_student_read_responses(),
        422: {"description": "Year and month were not sent together, or a value is out of range"},
    },
)
def read_own_calendar(
    current_user: StudentUser,
    db: DbSession,
    year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
    month: Annotated[int | None, Query(ge=1, le=12)] = None,
    subject_id: Annotated[int | None, Query(ge=1)] = None,
) -> AttendanceCalendarResponse:
    """Return the signed-in student's marks laid out by day for one month."""
    selected_year, selected_month = _selected_month(year, month)
    student_id = _own_student(current_user, db)
    try:
        totals, days = student_attendance_calendar(
            db,
            actor=current_user,
            student_id=student_id,
            year=selected_year,
            month=selected_month,
            subject_id=subject_id,
        )
    except AttendanceError as exc:
        _reject(exc)
    summary = to_percentage(totals)
    return AttendanceCalendarResponse(
        year=selected_year,
        month=selected_month,
        present=summary.present,
        absent=summary.absent,
        late=summary.late,
        excused=summary.excused,
        total=summary.total,
        percentage=summary.percentage,
        days=[to_calendar_day(day, counts) for day, counts in days],
    )


def _teacher_responses() -> dict[int, dict[str, str]]:
    return {
        401: {"description": "Missing, expired, or invalid access token"},
        403: {"description": "Signed in as an admin or a student"},
        404: {"description": "No teacher profile, or the record is not assigned to this teacher"},
    }


@router.post(
    "/teacher/attendance/sessions",
    response_model=AttendanceSessionResponse,
    responses={
        **_teacher_responses(),
        409: {"description": "Class or subject is not active, or the lesson has no teacher"},
    },
    tags=["teacher"],
)
def start_session(
    body: AttendanceSessionStartRequest,
    request: Request,
    response: Response,
    current_user: TeacherUser,
    db: DbSession,
) -> AttendanceSessionResponse:
    """Open today's attendance session for one assigned class and subject."""
    try:
        outcome = start_attendance_session(
            db,
            actor=current_user,
            class_id=body.class_id,
            subject_id=body.subject_id,
            ip_address=_client_ip(request),
        )
    except AttendanceError as exc:
        _reject(exc)
    if outcome.created:
        response.status_code = status.HTTP_201_CREATED
    return to_attendance_session(outcome.record, created=outcome.created)


@router.post(
    "/teacher/attendance/sessions/{session_id}/close",
    response_model=AttendanceSessionResponse,
    responses=_teacher_responses(),
    tags=["teacher"],
)
def close_session(
    session_id: SessionId,
    request: Request,
    current_user: TeacherUser,
    db: DbSession,
) -> AttendanceSessionResponse:
    """Close one of this teacher's attendance sessions."""
    try:
        record = close_attendance_session(
            db,
            actor=current_user,
            session_id=session_id,
            ip_address=_client_ip(request),
        )
    except AttendanceError as exc:
        _reject(exc)
    return to_attendance_session(record, created=False)


@router.get(
    "/teacher/attendance/today",
    response_model=AttendanceListResponse,
    responses=_teacher_responses(),
    tags=["teacher"],
)
def read_teacher_today(
    current_user: TeacherUser,
    db: DbSession,
    page: Page = 1,
    page_size: PageSize = 20,
    class_id: Annotated[int | None, Query(ge=1)] = None,
    subject_id: Annotated[int | None, Query(ge=1)] = None,
) -> AttendanceListResponse:
    """Return today's attendance for classes and subjects assigned to this teacher."""
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
    "/teacher/attendance/statistics",
    response_model=AttendanceStatisticsResponse,
    responses={
        **_teacher_responses(),
        422: {"description": "The date range ends before it starts"},
    },
    tags=["teacher"],
)
def read_teacher_statistics(
    current_user: TeacherUser,
    db: DbSession,
    class_id: Annotated[int | None, Query(ge=1)] = None,
    subject_id: Annotated[int | None, Query(ge=1)] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
) -> AttendanceStatisticsResponse:
    """Count present, absent, late, and excused marks this teacher can read."""
    try:
        counts = attendance_statistics(
            db,
            actor=current_user,
            class_id=class_id,
            subject_id=subject_id,
            date_from=date_from,
            date_to=date_to,
        )
    except AttendanceError as exc:
        _reject(exc)
    return to_statistics(counts)


@router.get(
    "/teacher/attendance/export",
    responses={
        **_teacher_responses(),
        422: {"description": "The date range ends before it starts"},
    },
    tags=["teacher"],
)
def export_teacher_attendance(
    current_user: TeacherUser,
    db: DbSession,
    class_id: Annotated[int | None, Query(ge=1)] = None,
    subject_id: Annotated[int | None, Query(ge=1)] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
) -> Response:
    """Download a CSV of attendance this teacher is allowed to read."""
    try:
        rows = export_attendance(
            db,
            actor=current_user,
            class_id=class_id,
            subject_id=subject_id,
            date_from=date_from,
            date_to=date_to,
        )
    except AttendanceError as exc:
        _reject(exc)
    return Response(
        content=_attendance_csv(rows),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="attendance-report.csv"'},
    )


@router.get(
    "/teacher/attendance",
    response_model=AttendanceListResponse,
    responses={
        **_teacher_responses(),
        422: {"description": "The date range ends before it starts"},
    },
    tags=["teacher"],
)
def read_teacher_history(
    current_user: TeacherUser,
    db: DbSession,
    page: Page = 1,
    page_size: PageSize = 20,
    class_id: Annotated[int | None, Query(ge=1)] = None,
    subject_id: Annotated[int | None, Query(ge=1)] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
) -> AttendanceListResponse:
    """Return this teacher's attendance history, newest date first."""
    try:
        rows, total = list_history(
            db,
            actor=current_user,
            class_id=class_id,
            subject_id=subject_id,
            date_from=date_from,
            date_to=date_to,
            page=page,
            page_size=page_size,
        )
    except AttendanceError as exc:
        _reject(exc)
    return _page(rows, total, page, page_size)


def _attendance_csv(rows) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "date",
            "class",
            "section",
            "subject",
            "subject_code",
            "roll_number",
            "first_name",
            "last_name",
            "status",
            "check_in_time",
            "recognition_method",
            "confidence_score",
        ]
    )
    for row in rows:
        student = row.student
        user = student.user
        writer.writerow(
            [
                _csv_cell(row.attendance_date.isoformat()),
                _csv_cell(row.school_class.name),
                _csv_cell(row.school_class.section),
                _csv_cell(row.subject.name),
                _csv_cell(row.subject.code),
                _csv_cell(student.roll_number),
                _csv_cell(user.first_name),
                _csv_cell(user.last_name),
                _csv_cell(row.status.value),
                _csv_cell(row.marked_at.isoformat()),
                _csv_cell(row.method.value),
                "" if row.confidence is None else _csv_cell(f"{float(row.confidence):.4f}"),
            ]
        )
    return buffer.getvalue()


def _csv_cell(value: str) -> str:
    """Keep spreadsheet formulas from running when the report is opened."""
    if value[:1] in ("=", "+", "-", "@"):
        return f"'{value}"
    return value


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
    if isinstance(exc, (ClassNotFound, SubjectNotFound, StudentNotFound, SessionNotFound)):
        detail = {
            ClassNotFound: "Class not found",
            SubjectNotFound: "Subject not found",
            StudentNotFound: "Student not found",
            SessionNotFound: "Attendance session not found",
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
