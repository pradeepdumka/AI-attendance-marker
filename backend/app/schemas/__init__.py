"""Pydantic schemas.

Request and response models for the HTTP API live here. These classes
validate and document JSON. They are not database tables.
"""

from app.schemas.academics import (
    ClassCreateRequest,
    ClassResponse,
    ClassUpdateRequest,
    EnrollmentResponse,
    SubjectCreateRequest,
    SubjectResponse,
    SubjectUpdateRequest,
)
from app.schemas.auth import (
    CurrentUserResponse,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
)
from app.schemas.health import HealthResponse
from app.schemas.student import (
    StudentCreateRequest,
    StudentListResponse,
    StudentResponse,
    StudentStatus,
    StudentUpdateRequest,
)
from app.schemas.teacher import (
    TeacherCreateRequest,
    TeacherListResponse,
    TeacherProfileUpdateRequest,
    TeacherResponse,
    TeacherStatus,
    TeacherUpdateRequest,
)

__all__ = [
    "ClassCreateRequest",
    "ClassResponse",
    "ClassUpdateRequest",
    "CurrentUserResponse",
    "EnrollmentResponse",
    "HealthResponse",
    "LoginRequest",
    "RegisterRequest",
    "StudentCreateRequest",
    "StudentListResponse",
    "StudentResponse",
    "StudentStatus",
    "StudentUpdateRequest",
    "SubjectCreateRequest",
    "SubjectResponse",
    "SubjectUpdateRequest",
    "TeacherCreateRequest",
    "TeacherListResponse",
    "TeacherProfileUpdateRequest",
    "TeacherResponse",
    "TeacherStatus",
    "TeacherUpdateRequest",
    "TokenResponse",
]
