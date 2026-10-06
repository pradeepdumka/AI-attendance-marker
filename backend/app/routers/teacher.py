"""Routes that only a teacher may call.

`GET /teacher/me` identifies the signed-in teacher account.
`GET /teacher/profile` returns that account's staff profile, and
`PATCH /teacher/profile` updates name, phone, and department.
Assigned classes and subjects are listed under `/teacher/classes` and
`/teacher/subjects`. Attendance marking is a later phase.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth.dependencies import TeacherUser
from app.database.connection import get_db
from app.models.user import User
from app.schemas.auth import CurrentUserResponse
from app.routers.teachers import _reject
from app.schemas.teacher import TeacherProfileUpdateRequest, TeacherResponse, to_teacher_response
from app.services.teachers import TeacherServiceError, get_teacher_for_user, update_teacher

router = APIRouter(prefix="/teacher", tags=["teacher"])

DbSession = Annotated[Session, Depends(get_db)]
_ADMIN_ONLY_FIELDS = frozenset({"email", "password", "employee_id", "status"})


@router.get(
    "/me",
    response_model=CurrentUserResponse,
    responses={
        401: {"description": "Missing, expired, or invalid access token"},
        403: {"description": "Signed in as an admin or a student"},
    },
)
def read_teacher(current_user: TeacherUser) -> User:
    """Return the current user when that user is a teacher."""
    return current_user


@router.get(
    "/profile",
    response_model=TeacherResponse,
    responses={
        401: {"description": "Missing, expired, or invalid access token"},
        403: {"description": "Signed in as an admin or a student"},
        404: {"description": "This account has no teacher profile"},
    },
)
def read_teacher_profile(current_user: TeacherUser, db: DbSession) -> TeacherResponse:
    """Return the staff profile linked to the signed-in teacher."""
    teacher = get_teacher_for_user(db, current_user.id)
    if teacher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Teacher profile not found",
        )
    return to_teacher_response(teacher)


@router.patch(
    "/profile",
    response_model=TeacherResponse,
    responses={
        401: {"description": "Missing, expired, or invalid access token"},
        403: {"description": "Signed in as an admin or a student, or a reserved field was sent"},
        404: {"description": "This account has no teacher profile"},
        422: {"description": "Invalid profile fields"},
    },
)
def update_teacher_profile(
    body: TeacherProfileUpdateRequest,
    request: Request,
    current_user: TeacherUser,
    db: DbSession,
) -> TeacherResponse:
    """Update the signed-in teacher's name, phone, or department."""
    if body.model_fields_set & _ADMIN_ONLY_FIELDS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot change that profile field",
        )
    teacher = get_teacher_for_user(db, current_user.id)
    if teacher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Teacher profile not found",
        )
    try:
        updated = update_teacher(
            db,
            actor_id=current_user.id,
            teacher_id=teacher.id,
            changes=_profile_changes(body),
            self_service=True,
            ip_address=_client_ip(request),
        )
    except TeacherServiceError as exc:
        _reject(exc)
    return to_teacher_response(updated)


def _profile_changes(body: TeacherProfileUpdateRequest) -> dict[str, object]:
    changes: dict[str, object] = {}
    fields = body.model_fields_set
    if "first_name" in fields:
        changes["first_name"] = body.first_name
    if "last_name" in fields:
        changes["last_name"] = body.last_name
    if "phone" in fields:
        changes["phone"] = body.phone
    if "department" in fields:
        changes["department"] = body.department
    return changes


def _client_ip(request: Request) -> str | None:
    if request.client is None:
        return None
    host = request.client.host
    return host[:45] if host else None
