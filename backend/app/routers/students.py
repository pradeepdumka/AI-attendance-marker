"""Admin routes for the student roster.

Creating a student opens a STUDENT login and a profile. Deleting a
student deactivates that login and keeps the profile. These routes do
not store face encodings.
"""

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.auth.dependencies import AdminUser
from app.database.connection import get_db
from app.models.student import Gender
from app.schemas.student import (
    StudentCreateRequest,
    StudentListResponse,
    StudentResponse,
    StudentStatus,
    StudentUpdateRequest,
    to_student_response,
)
from app.services.students import (
    ClassNotActive,
    ClassNotFound,
    DuplicateRollNumber,
    EmailAlreadyUsed,
    NewStudent,
    PasswordMatchesEmail,
    StudentNotFound,
    StudentServiceError,
    create_student as create_student_record,
    deactivate_student as deactivate_student_record,
    get_student as get_student_record,
    list_students as list_student_records,
    update_student as update_student_record,
)

router = APIRouter(prefix="/admin/students", tags=["students"])

DbSession = Annotated[Session, Depends(get_db)]
StudentId = Annotated[int, Path(ge=1, description="Student profile id")]


@router.post(
    "",
    response_model=StudentResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"description": "Missing, expired, or invalid access token"},
        403: {"description": "Signed in as a teacher or a student"},
        404: {"description": "Class not found"},
        409: {"description": "Email, roll number, or inactive class conflict"},
        422: {"description": "Invalid student fields"},
    },
)
def create_student(
    body: StudentCreateRequest,
    request: Request,
    response: Response,
    current_user: AdminUser,
    db: DbSession,
) -> StudentResponse:
    """Create a student account, profile, and optional class enrollment."""
    try:
        student = create_student_record(
            db,
            actor_id=current_user.id,
            student=NewStudent(
                first_name=body.first_name,
                last_name=body.last_name,
                email=body.email,
                password=body.password.get_secret_value(),
                phone=body.phone,
                roll_number=body.roll_number,
                date_of_birth=body.date_of_birth,
                gender=body.gender,
                class_id=body.class_id,
                is_active=body.status == StudentStatus.ACTIVE,
            ),
            ip_address=_client_ip(request),
        )
    except StudentServiceError as exc:
        _reject(exc)
    response.headers["Location"] = f"/admin/students/{student.id}"
    return to_student_response(student)


@router.get(
    "",
    response_model=StudentListResponse,
    responses={
        401: {"description": "Missing, expired, or invalid access token"},
        403: {"description": "Signed in as a teacher or a student"},
        422: {"description": "Invalid page, page size, or filter"},
    },
)
def list_students(
    _admin: AdminUser,
    db: DbSession,
    search: Annotated[str | None, Query(max_length=100)] = None,
    account_status: Annotated[StudentStatus | None, Query(alias="status")] = None,
    class_id: Annotated[int | None, Query(ge=1)] = None,
    gender: Gender | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> StudentListResponse:
    """List students, optionally filtered by search text, status, class, or gender."""
    is_active = None if account_status is None else account_status == StudentStatus.ACTIVE
    rows, total = list_student_records(
        db,
        search=search,
        is_active=is_active,
        class_id=class_id,
        gender=gender,
        page=page,
        page_size=page_size,
    )
    return StudentListResponse(
        items=[to_student_response(student) for student in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{student_id}",
    response_model=StudentResponse,
    responses={
        401: {"description": "Missing, expired, or invalid access token"},
        403: {"description": "Signed in as a teacher or a student"},
        404: {"description": "Student not found"},
    },
)
def read_student(
    student_id: StudentId,
    _admin: AdminUser,
    db: DbSession,
) -> StudentResponse:
    """Return one student, including a deactivated profile."""
    try:
        student = get_student_record(db, student_id)
    except StudentServiceError as exc:
        _reject(exc)
    return to_student_response(student)


@router.patch(
    "/{student_id}",
    response_model=StudentResponse,
    responses={
        401: {"description": "Missing, expired, or invalid access token"},
        403: {"description": "Signed in as a teacher or a student"},
        404: {"description": "Student or class not found"},
        409: {"description": "Email, roll number, or inactive class conflict"},
        422: {"description": "Invalid student fields"},
    },
)
def update_student(
    student_id: StudentId,
    body: StudentUpdateRequest,
    request: Request,
    current_user: AdminUser,
    db: DbSession,
) -> StudentResponse:
    """Update account, profile, status, or current class."""
    try:
        student = update_student_record(
            db,
            actor_id=current_user.id,
            student_id=student_id,
            changes=_changes(body),
            ip_address=_client_ip(request),
        )
    except StudentServiceError as exc:
        _reject(exc)
    return to_student_response(student)


@router.delete(
    "/{student_id}",
    response_model=StudentResponse,
    responses={
        401: {"description": "Missing, expired, or invalid access token"},
        403: {"description": "Signed in as a teacher or a student"},
        404: {"description": "Student not found"},
    },
)
def deactivate_student(
    student_id: StudentId,
    request: Request,
    current_user: AdminUser,
    db: DbSession,
) -> StudentResponse:
    """Deactivate the student account. The roster row is not removed."""
    try:
        student = deactivate_student_record(
            db,
            actor_id=current_user.id,
            student_id=student_id,
            ip_address=_client_ip(request),
        )
    except StudentServiceError as exc:
        _reject(exc)
    return to_student_response(student)


def _changes(body: StudentUpdateRequest) -> dict[str, object]:
    changes: dict[str, object] = {}
    fields = body.model_fields_set
    if "first_name" in fields:
        changes["first_name"] = body.first_name
    if "last_name" in fields:
        changes["last_name"] = body.last_name
    if "email" in fields:
        changes["email"] = body.email
    if "phone" in fields:
        changes["phone"] = body.phone
    if "password" in fields and body.password is not None:
        changes["password"] = body.password.get_secret_value()
    if "roll_number" in fields:
        changes["roll_number"] = body.roll_number
    if "date_of_birth" in fields:
        changes["date_of_birth"] = body.date_of_birth
    if "gender" in fields:
        changes["gender"] = body.gender
    if "class_id" in fields:
        changes["class_id"] = body.class_id
    if "status" in fields and body.status is not None:
        changes["is_active"] = body.status == StudentStatus.ACTIVE
    return changes


def _client_ip(request: Request) -> str | None:
    if request.client is None:
        return None
    host = request.client.host
    return host[:45] if host else None


def _reject(exc: StudentServiceError) -> NoReturn:
    if isinstance(exc, StudentNotFound):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found") from None
    if isinstance(exc, ClassNotFound):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Class not found") from None
    if isinstance(exc, ClassNotActive):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Class is not active") from None
    if isinstance(exc, EmailAlreadyUsed):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        ) from None
    if isinstance(exc, DuplicateRollNumber):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A student with this roll number already exists",
        ) from None
    if isinstance(exc, PasswordMatchesEmail):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Password must not match the email address",
        ) from None
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Student request failed",
    ) from exc
