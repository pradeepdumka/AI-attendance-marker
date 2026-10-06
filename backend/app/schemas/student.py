"""Request and response models for the student roster.

A student response joins the user account (name, email, phone, sign-in
status) with the student profile (roll number, date of birth, gender)
and the current active class. Passwords and password hashes are not
part of this contract.
"""

import enum
from datetime import date, datetime
from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, Field, model_validator

from app.models.base import utcnow

from app.models.enrollment import EnrollmentStatus
from app.models.student import Gender, Student
from app.schemas.auth import (
    EmailAddress,
    PersonName,
    PhoneNumber,
    RegistrationPassword,
)


class StudentStatus(enum.Enum):
    """Whether the student account can sign in.

    `ACTIVE` is `users.is_active`. Deactivating a student sets `INACTIVE`
    and keeps the roster row.
    """

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


def _date_of_birth(value: date | None) -> date | None:
    if value is None:
        return None
    today = utcnow().date()
    if value > today:
        raise ValueError("Date of birth cannot be in the future")
    if value < date(today.year - 120, 1, 1):
        raise ValueError("Date of birth is too far in the past")
    return value


def _normalize_roll_number(value: str) -> str:
    roll_number = " ".join(value.split())
    if roll_number == "":
        raise ValueError("Enter a roll number")
    if len(roll_number) > 50:
        raise ValueError("Roll number must be at most 50 characters")
    return roll_number


DateOfBirth = Annotated[date | None, AfterValidator(_date_of_birth)]
RollNumber = Annotated[
    str,
    Field(min_length=1, max_length=80, examples=["2026-014"]),
    AfterValidator(_normalize_roll_number),
]
ClassId = Annotated[int, Field(ge=1, examples=[1])]

_NULL_IS_INVALID = (
    "first_name",
    "last_name",
    "email",
    "roll_number",
    "password",
    "status",
)


class StudentCreateRequest(BaseModel):
    """Account and roster fields for `POST /admin/students`.

    The password is write-only. This route always creates a `STUDENT`
    user. It does not store a face encoding.
    """

    first_name: PersonName = Field(examples=["Ada"])
    last_name: PersonName = Field(examples=["Lovelace"])
    email: EmailAddress
    password: RegistrationPassword
    phone: PhoneNumber = None
    roll_number: RollNumber
    date_of_birth: DateOfBirth = None
    gender: Gender | None = None
    class_id: ClassId | None = None
    status: StudentStatus = StudentStatus.ACTIVE

    @model_validator(mode="after")
    def password_is_not_the_email(self) -> Self:
        if self.password.get_secret_value().casefold() == self.email.casefold():
            raise ValueError("Password must not match the email address")
        return self


class StudentUpdateRequest(BaseModel):
    """Partial update for `PATCH /admin/students/{student_id}`.

    Omitted fields stay as they are. `phone`, `date_of_birth`, `gender`,
    and `class_id` can be cleared with null. A null `class_id` withdraws
    the current class enrollment.
    """

    first_name: PersonName | None = None
    last_name: PersonName | None = None
    email: EmailAddress | None = None
    password: RegistrationPassword | None = None
    phone: PhoneNumber = None
    roll_number: RollNumber | None = None
    date_of_birth: DateOfBirth = None
    gender: Gender | None = None
    class_id: ClassId | None = None
    status: StudentStatus | None = None

    @model_validator(mode="after")
    def null_only_clears_optional_fields(self) -> Self:
        for field_name in _NULL_IS_INVALID:
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        if (
            self.password is not None
            and self.email is not None
            and self.password.get_secret_value().casefold() == self.email.casefold()
        ):
            raise ValueError("Password must not match the email address")
        return self


class StudentResponse(BaseModel):
    """One roster row. `student_id` is the students table primary key."""

    student_id: int
    roll_number: str
    first_name: str
    last_name: str
    email: str
    phone: str | None
    date_of_birth: date | None
    gender: Gender | None
    class_id: int | None
    status: StudentStatus
    created_at: datetime
    updated_at: datetime


class StudentListResponse(BaseModel):
    """One page of students plus the unpaged total for the same filters."""

    items: list[StudentResponse]
    total: int
    page: int
    page_size: int


def to_student_response(student: Student) -> StudentResponse:
    """Build the public student record from a loaded profile and user."""
    user = student.user
    return StudentResponse(
        student_id=student.id,
        roll_number=student.roll_number,
        first_name=user.first_name,
        last_name=user.last_name,
        email=user.email,
        phone=user.phone,
        date_of_birth=student.date_of_birth,
        gender=student.gender,
        class_id=_current_class_id(student),
        status=StudentStatus.ACTIVE if user.is_active else StudentStatus.INACTIVE,
        created_at=student.created_at,
        updated_at=student.updated_at,
    )


def _current_class_id(student: Student) -> int | None:
    active = [
        enrollment
        for enrollment in student.enrollments
        if enrollment.status == EnrollmentStatus.ACTIVE
    ]
    if not active:
        return None
    current = max(active, key=lambda enrollment: (enrollment.enrolled_on, enrollment.id))
    return current.class_id
