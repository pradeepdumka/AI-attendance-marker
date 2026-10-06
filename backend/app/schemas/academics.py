"""Request and response models for classes, subjects, and enrollment.

A class is one section for an academic year. A subject belongs to one
class. `class_teacher_id` and `teacher_id` are single assignments, so
the same teacher cannot be stored twice on one class or one subject.
"""

import enum
from datetime import date, datetime
from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, Field, model_validator

from app.models.class_model import SchoolClass
from app.models.enrollment import Enrollment, EnrollmentStatus
from app.models.subject import Subject


class RecordStatus(enum.Enum):
    """Whether a class or subject is open for use."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


def _required_label(value: str, *, empty: str, limit: int, too_long: str) -> str:
    label = " ".join(value.split())
    if label == "":
        raise ValueError(empty)
    if len(label) > limit:
        raise ValueError(too_long)
    return label


def _class_name(value: str) -> str:
    return _required_label(
        value,
        empty="Enter a class name",
        limit=100,
        too_long="Class name must be at most 100 characters",
    )


def _section(value: str) -> str:
    return _required_label(
        value,
        empty="Enter a section",
        limit=20,
        too_long="Section must be at most 20 characters",
    )


def _academic_year(value: str) -> str:
    return _required_label(
        value,
        empty="Enter an academic year",
        limit=20,
        too_long="Academic year must be at most 20 characters",
    )


def _subject_name(value: str) -> str:
    return _required_label(
        value,
        empty="Enter a subject name",
        limit=150,
        too_long="Subject name must be at most 150 characters",
    )


def _subject_code(value: str) -> str:
    return _required_label(
        value,
        empty="Enter a subject code",
        limit=50,
        too_long="Subject code must be at most 50 characters",
    )


ClassName = Annotated[str, Field(min_length=1, max_length=120, examples=["Grade 10"]), AfterValidator(_class_name)]
Section = Annotated[str, Field(min_length=1, max_length=40, examples=["A"]), AfterValidator(_section)]
AcademicYear = Annotated[
    str,
    Field(min_length=1, max_length=40, examples=["2026-2027"]),
    AfterValidator(_academic_year),
]
SubjectName = Annotated[str, Field(min_length=1, max_length=180, examples=["Mathematics"]), AfterValidator(_subject_name)]
SubjectCode = Annotated[str, Field(min_length=1, max_length=80, examples=["MATH"]), AfterValidator(_subject_code)]
RecordId = Annotated[int, Field(ge=1)]

_CLASS_NULL_IS_INVALID = ("name", "section", "academic_year", "status")
_SUBJECT_NULL_IS_INVALID = ("class_id", "name", "code", "status")


class ClassCreateRequest(BaseModel):
    """Fields for `POST /admin/classes`."""

    name: ClassName
    section: Section
    academic_year: AcademicYear
    class_teacher_id: RecordId | None = None
    status: RecordStatus = RecordStatus.ACTIVE


class ClassUpdateRequest(BaseModel):
    """Partial update for `PATCH /admin/classes/{class_id}`.

    Omitted fields stay as they are. `class_teacher_id` can be cleared
    with null.
    """

    name: ClassName | None = None
    section: Section | None = None
    academic_year: AcademicYear | None = None
    class_teacher_id: RecordId | None = None
    status: RecordStatus | None = None

    @model_validator(mode="after")
    def null_only_clears_the_teacher(self) -> Self:
        for field_name in _CLASS_NULL_IS_INVALID:
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        return self


class ClassResponse(BaseModel):
    """One class section."""

    class_id: int
    name: str
    section: str
    academic_year: str
    class_teacher_id: int | None
    status: RecordStatus
    created_at: datetime
    updated_at: datetime


class ClassListResponse(BaseModel):
    """One page of classes plus the unpaged total for the same filters."""

    items: list[ClassResponse]
    total: int
    page: int
    page_size: int


class SubjectCreateRequest(BaseModel):
    """Fields for `POST /admin/subjects`."""

    class_id: RecordId
    name: SubjectName
    code: SubjectCode
    teacher_id: RecordId | None = None
    status: RecordStatus = RecordStatus.ACTIVE


class SubjectUpdateRequest(BaseModel):
    """Partial update for `PATCH /admin/subjects/{subject_id}`.

    Omitted fields stay as they are. `teacher_id` can be cleared with null.
    """

    class_id: RecordId | None = None
    name: SubjectName | None = None
    code: SubjectCode | None = None
    teacher_id: RecordId | None = None
    status: RecordStatus | None = None

    @model_validator(mode="after")
    def null_only_clears_the_teacher(self) -> Self:
        for field_name in _SUBJECT_NULL_IS_INVALID:
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        return self


class SubjectResponse(BaseModel):
    """One subject offered to a class."""

    subject_id: int
    class_id: int
    name: str
    code: str
    teacher_id: int | None
    status: RecordStatus
    created_at: datetime
    updated_at: datetime


class SubjectListResponse(BaseModel):
    """One page of subjects plus the unpaged total for the same filters."""

    items: list[SubjectResponse]
    total: int
    page: int
    page_size: int


class TeacherAssignmentRequest(BaseModel):
    """Teacher placed in the single class or subject slot."""

    teacher_id: RecordId


class EnrollStudentRequest(BaseModel):
    """Student to enroll in the class named by the URL."""

    student_id: RecordId


class EnrollmentResponse(BaseModel):
    """One student membership in a class. The pair cannot be stored twice."""

    enrollment_id: int
    student_id: int
    class_id: int
    roll_number: str
    first_name: str
    last_name: str
    status: EnrollmentStatus
    enrolled_on: date


class EnrollmentListResponse(BaseModel):
    """One page of class memberships."""

    items: list[EnrollmentResponse]
    total: int
    page: int
    page_size: int


def to_class_response(school_class: SchoolClass) -> ClassResponse:
    """Build the public class record."""
    return ClassResponse(
        class_id=school_class.id,
        name=school_class.name,
        section=school_class.section,
        academic_year=school_class.academic_year,
        class_teacher_id=school_class.class_teacher_id,
        status=RecordStatus.ACTIVE if school_class.is_active else RecordStatus.INACTIVE,
        created_at=school_class.created_at,
        updated_at=school_class.updated_at,
    )


def to_subject_response(subject: Subject) -> SubjectResponse:
    """Build the public subject record."""
    return SubjectResponse(
        subject_id=subject.id,
        class_id=subject.class_id,
        name=subject.name,
        code=subject.code,
        teacher_id=subject.teacher_id,
        status=RecordStatus.ACTIVE if subject.is_active else RecordStatus.INACTIVE,
        created_at=subject.created_at,
        updated_at=subject.updated_at,
    )


def to_enrollment_response(enrollment: Enrollment) -> EnrollmentResponse:
    """Build the public enrollment record. The student and user must be loaded."""
    student = enrollment.student
    user = student.user
    return EnrollmentResponse(
        enrollment_id=enrollment.id,
        student_id=student.id,
        class_id=enrollment.class_id,
        roll_number=student.roll_number,
        first_name=user.first_name,
        last_name=user.last_name,
        status=enrollment.status,
        enrolled_on=enrollment.enrolled_on,
    )
