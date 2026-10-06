"""Admin routes for teacher profiles.

Creating a teacher opens a TEACHER login and a profile. Deleting a
teacher deactivates that login and keeps the profile. These routes do
not assign classes or subjects.
"""

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.auth.dependencies import AdminUser
from app.database.connection import get_db
from app.schemas.teacher import (
    TeacherCreateRequest,
    TeacherListResponse,
    TeacherResponse,
    TeacherStatus,
    TeacherUpdateRequest,
    to_teacher_response,
)
from app.services.teachers import (
    DuplicateEmployeeId,
    EmailAlreadyUsed,
    NewTeacher,
    PasswordMatchesEmail,
    TeacherFieldNotAllowed,
    TeacherNotFound,
    TeacherServiceError,
    create_teacher as create_teacher_record,
    deactivate_teacher as deactivate_teacher_record,
    get_teacher as get_teacher_record,
    list_teachers as list_teacher_records,
    update_teacher as update_teacher_record,
)

router = APIRouter(prefix="/admin/teachers", tags=["teachers"])

DbSession = Annotated[Session, Depends(get_db)]
TeacherId = Annotated[int, Path(ge=1, description="Teacher profile id")]


@router.post(
    "",
    response_model=TeacherResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"description": "Missing, expired, or invalid access token"},
        403: {"description": "Signed in as a teacher or a student"},
        409: {"description": "Email or employee id already exists"},
        422: {"description": "Invalid teacher fields"},
    },
)
def create_teacher(
    body: TeacherCreateRequest,
    request: Request,
    response: Response,
    current_user: AdminUser,
    db: DbSession,
) -> TeacherResponse:
    """Create a teacher account and profile."""
    try:
        teacher = create_teacher_record(
            db,
            actor_id=current_user.id,
            teacher=NewTeacher(
                first_name=body.first_name,
                last_name=body.last_name,
                email=body.email,
                password=body.password.get_secret_value(),
                phone=body.phone,
                employee_id=body.employee_id,
                department=body.department,
                is_active=body.status == TeacherStatus.ACTIVE,
            ),
            ip_address=_client_ip(request),
        )
    except TeacherServiceError as exc:
        _reject(exc)
    response.headers["Location"] = f"/admin/teachers/{teacher.id}"
    return to_teacher_response(teacher)


@router.get(
    "",
    response_model=TeacherListResponse,
    responses={
        401: {"description": "Missing, expired, or invalid access token"},
        403: {"description": "Signed in as a teacher or a student"},
        422: {"description": "Invalid page, page size, or filter"},
    },
)
def list_teachers(
    _admin: AdminUser,
    db: DbSession,
    search: Annotated[str | None, Query(max_length=100)] = None,
    account_status: Annotated[TeacherStatus | None, Query(alias="status")] = None,
    department: Annotated[str | None, Query(max_length=100)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> TeacherListResponse:
    """List teachers, optionally filtered by search text, status, or department."""
    is_active = None if account_status is None else account_status == TeacherStatus.ACTIVE
    rows, total = list_teacher_records(
        db,
        search=search,
        is_active=is_active,
        department=department,
        page=page,
        page_size=page_size,
    )
    return TeacherListResponse(
        items=[to_teacher_response(teacher) for teacher in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{teacher_id}",
    response_model=TeacherResponse,
    responses={
        401: {"description": "Missing, expired, or invalid access token"},
        403: {"description": "Signed in as a teacher or a student"},
        404: {"description": "Teacher not found"},
    },
)
def read_teacher(teacher_id: TeacherId, _admin: AdminUser, db: DbSession) -> TeacherResponse:
    """Return one teacher, including a deactivated profile."""
    try:
        teacher = get_teacher_record(db, teacher_id)
    except TeacherServiceError as exc:
        _reject(exc)
    return to_teacher_response(teacher)


@router.patch(
    "/{teacher_id}",
    response_model=TeacherResponse,
    responses={
        401: {"description": "Missing, expired, or invalid access token"},
        403: {"description": "Signed in as a teacher or a student"},
        409: {"description": "Email or employee id already exists"},
        422: {"description": "Invalid teacher fields"},
    },
)
def update_teacher(
    teacher_id: TeacherId,
    body: TeacherUpdateRequest,
    request: Request,
    current_user: AdminUser,
    db: DbSession,
) -> TeacherResponse:
    """Update the teacher account or profile."""
    try:
        teacher = update_teacher_record(
            db,
            actor_id=current_user.id,
            teacher_id=teacher_id,
            changes=_changes(body),
            ip_address=_client_ip(request),
        )
    except TeacherServiceError as exc:
        _reject(exc)
    return to_teacher_response(teacher)


@router.delete(
    "/{teacher_id}",
    response_model=TeacherResponse,
    responses={
        401: {"description": "Missing, expired, or invalid access token"},
        403: {"description": "Signed in as a teacher or a student"},
        404: {"description": "Teacher not found"},
    },
)
def deactivate_teacher(
    teacher_id: TeacherId,
    request: Request,
    current_user: AdminUser,
    db: DbSession,
) -> TeacherResponse:
    """Deactivate the teacher account. The profile row is not removed."""
    try:
        teacher = deactivate_teacher_record(
            db,
            actor_id=current_user.id,
            teacher_id=teacher_id,
            ip_address=_client_ip(request),
        )
    except TeacherServiceError as exc:
        _reject(exc)
    return to_teacher_response(teacher)


def _changes(body: TeacherUpdateRequest) -> dict[str, object]:
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
    if "employee_id" in fields:
        changes["employee_id"] = body.employee_id
    if "department" in fields:
        changes["department"] = body.department
    if "status" in fields and body.status is not None:
        changes["is_active"] = body.status == TeacherStatus.ACTIVE
    return changes


def _client_ip(request: Request) -> str | None:
    if request.client is None:
        return None
    host = request.client.host
    return host[:45] if host else None


def _reject(exc: TeacherServiceError) -> NoReturn:
    if isinstance(exc, TeacherNotFound):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Teacher not found") from None
    if isinstance(exc, EmailAlreadyUsed):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        ) from None
    if isinstance(exc, DuplicateEmployeeId):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A teacher with this employee id already exists",
        ) from None
    if isinstance(exc, PasswordMatchesEmail):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Password must not match the email address",
        ) from None
    if isinstance(exc, TeacherFieldNotAllowed):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot change that profile field",
        ) from None
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Teacher request failed",
    ) from exc
