"""Request and response models for teacher profiles.

A teacher response joins the user account with the staff profile.
`employee_id` is stored as `teachers.employee_code`. Passwords and
password hashes are not part of this contract. Class assignment is not
part of this contract.
"""

import enum
from datetime import datetime
from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from app.models.teacher import Teacher
from app.schemas.auth import EmailAddress, PersonName, PhoneNumber, RegistrationPassword


class TeacherStatus(enum.Enum):
    """Whether the teacher account can sign in.

    `ACTIVE` is `users.is_active`. Deactivating a teacher sets `INACTIVE`
    and keeps the profile row.
    """

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


def _normalize_employee_id(value: str) -> str:
    employee_id = " ".join(value.split())
    if employee_id == "":
        raise ValueError("Enter an employee id")
    if len(employee_id) > 50:
        raise ValueError("Employee id must be at most 50 characters")
    return employee_id


def _normalize_department(value: str | None) -> str | None:
    if value is None:
        return None
    department = " ".join(value.split())
    if department == "":
        return None
    if len(department) > 100:
        raise ValueError("Department must be at most 100 characters")
    return department


EmployeeId = Annotated[
    str,
    Field(min_length=1, max_length=80, examples=["T-014"]),
    AfterValidator(_normalize_employee_id),
]
Department = Annotated[
    str | None,
    Field(max_length=150, examples=["Mathematics"]),
    AfterValidator(_normalize_department),
]

_NULL_IS_INVALID = (
    "first_name",
    "last_name",
    "email",
    "employee_id",
    "password",
    "status",
)


class TeacherCreateRequest(BaseModel):
    """Account and profile fields for `POST /admin/teachers`.

    The password is write-only. This route always creates a `TEACHER`
    user and does not assign a class.
    """

    first_name: PersonName = Field(examples=["Grace"])
    last_name: PersonName = Field(examples=["Hopper"])
    email: EmailAddress
    password: RegistrationPassword
    phone: PhoneNumber = None
    employee_id: EmployeeId
    department: Department = None
    status: TeacherStatus = TeacherStatus.ACTIVE

    @model_validator(mode="after")
    def password_is_not_the_email(self) -> Self:
        if self.password.get_secret_value().casefold() == self.email.casefold():
            raise ValueError("Password must not match the email address")
        return self


class TeacherUpdateRequest(BaseModel):
    """Partial update for `PATCH /admin/teachers/{teacher_id}`.

    Omitted fields stay as they are. `phone` and `department` can be
    cleared with null.
    """

    first_name: PersonName | None = None
    last_name: PersonName | None = None
    email: EmailAddress | None = None
    password: RegistrationPassword | None = None
    phone: PhoneNumber = None
    employee_id: EmployeeId | None = None
    department: Department = None
    status: TeacherStatus | None = None

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


class TeacherProfileUpdateRequest(BaseModel):
    """Fields a teacher may change on `PATCH /teacher/profile`.

    Employee id, email, password, and status are present so a request
    that includes them can be rejected. They are not applied.
    """

    model_config = ConfigDict(extra="forbid")

    first_name: PersonName | None = None
    last_name: PersonName | None = None
    phone: PhoneNumber = None
    department: Department = None
    email: EmailAddress | None = None
    password: RegistrationPassword | None = None
    employee_id: EmployeeId | None = None
    status: TeacherStatus | None = None

    @model_validator(mode="after")
    def names_cannot_be_null(self) -> Self:
        for field_name in ("first_name", "last_name"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        return self


class TeacherResponse(BaseModel):
    """One staff profile. `teacher_id` is the teachers table primary key."""

    teacher_id: int
    employee_id: str
    first_name: str
    last_name: str
    email: str
    phone: str | None
    department: str | None
    status: TeacherStatus
    created_at: datetime
    updated_at: datetime


class TeacherListResponse(BaseModel):
    """One page of teachers plus the unpaged total for the same filters."""

    items: list[TeacherResponse]
    total: int
    page: int
    page_size: int


def to_teacher_response(teacher: Teacher) -> TeacherResponse:
    """Build the public teacher record from a loaded profile and user."""
    user = teacher.user
    return TeacherResponse(
        teacher_id=teacher.id,
        employee_id=teacher.employee_code,
        first_name=user.first_name,
        last_name=user.last_name,
        email=user.email,
        phone=user.phone,
        department=teacher.department,
        status=TeacherStatus.ACTIVE if user.is_active else TeacherStatus.INACTIVE,
        created_at=teacher.created_at,
        updated_at=teacher.updated_at,
    )
