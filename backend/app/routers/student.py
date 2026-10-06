"""Routes that only a student may call.

`GET /student/me` identifies the signed-in student account.
`GET /student/profile` returns that account's roster profile.
`GET /student/attendance` returns that student's attendance history.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.dependencies import StudentUser
from app.database.connection import get_db
from app.models.user import User
from app.schemas.auth import CurrentUserResponse
from app.schemas.student import StudentResponse, to_student_response
from app.services.students import get_student_for_user

router = APIRouter(prefix="/student", tags=["student"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get(
    "/me",
    response_model=CurrentUserResponse,
    responses={
        401: {"description": "Missing, expired, or invalid access token"},
        403: {"description": "Signed in as an admin or a teacher"},
    },
)
def read_student(current_user: StudentUser) -> User:
    """Return the current user when that user is a student."""
    return current_user


@router.get(
    "/profile",
    response_model=StudentResponse,
    responses={
        401: {"description": "Missing, expired, or invalid access token"},
        403: {"description": "Signed in as an admin or a teacher"},
        404: {"description": "This account has no student profile"},
    },
)
def read_student_profile(current_user: StudentUser, db: DbSession) -> StudentResponse:
    """Return the roster profile linked to the signed-in student."""
    student = get_student_for_user(db, current_user.id)
    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Student profile not found",
        )
    return to_student_response(student)
