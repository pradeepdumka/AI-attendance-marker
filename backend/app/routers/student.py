"""Routes that only a student may call.

`GET /student/me` identifies the signed-in student account.
`GET /student/profile` returns that account's roster profile.
`GET /student/class` returns the active enrollment.
`GET /student/subjects` lists active subjects in that class.
Attendance history, percentage, month, and calendar live under
`/student/attendance`.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth.dependencies import StudentUser
from app.database.connection import get_db
from app.models.student import Student
from app.models.user import User
from app.schemas.academics import (
    ClassResponse,
    SubjectListResponse,
    to_class_response,
    to_subject_response,
)
from app.schemas.auth import CurrentUserResponse
from app.schemas.student import StudentResponse, to_student_response
from app.services.academics import enrolled_class, list_subjects
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
    return to_student_response(_own_profile(db, current_user.id))


def _student_responses() -> dict[int, dict[str, str]]:
    return {
        401: {"description": "Missing, expired, or invalid access token"},
        403: {"description": "Signed in as an admin or a teacher"},
        404: {"description": "This account has no student profile"},
    }


@router.get(
    "/class",
    response_model=ClassResponse,
    responses={
        **_student_responses(),
        404: {"description": "No student profile, or the student is not enrolled"},
    },
)
def read_enrolled_class(current_user: StudentUser, db: DbSession) -> ClassResponse:
    """Return the class this student is currently enrolled in."""
    student = _own_profile(db, current_user.id)
    school_class = enrolled_class(db, student.id)
    if school_class is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You are not enrolled in a class",
        )
    return to_class_response(school_class)


@router.get(
    "/subjects",
    response_model=SubjectListResponse,
    responses=_student_responses(),
)
def read_enrolled_subjects(
    current_user: StudentUser,
    db: DbSession,
    search: Annotated[str | None, Query(max_length=100)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> SubjectListResponse:
    """List active subjects in the signed-in student's current class."""
    student = _own_profile(db, current_user.id)
    school_class = enrolled_class(db, student.id)
    if school_class is None:
        return SubjectListResponse(items=[], total=0, page=page, page_size=page_size)
    rows, total = list_subjects(
        db,
        search=search,
        is_active=True,
        class_id=school_class.id,
        teacher_id=None,
        page=page,
        page_size=page_size,
    )
    return SubjectListResponse(
        items=[to_subject_response(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


def _own_profile(db: Session, user_id: int) -> Student:
    student = get_student_for_user(db, user_id)
    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Student profile not found",
        )
    return student
