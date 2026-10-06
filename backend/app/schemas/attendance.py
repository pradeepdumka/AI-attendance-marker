"""Request and response models for attendance.

A mark stores the lesson, the check-in, and how the student was recognized.
The embedding and the camera frame are not part of the response.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.attendance import Attendance, AttendanceMethod, AttendanceStatus


class ManualAttendanceRequest(BaseModel):
    """Staff mark for a known student. The camera is not used."""

    student_id: int = Field(ge=1)
    class_id: int = Field(ge=1)
    subject_id: int = Field(ge=1)
    status: AttendanceStatus


class AttendanceResponse(BaseModel):
    """One stored attendance row."""

    attendance_id: int
    student_id: int
    roll_number: str
    first_name: str
    last_name: str
    class_id: int
    subject_id: int
    teacher_id: int | None
    date: date
    check_in_time: datetime
    status: AttendanceStatus
    confidence_score: float | None
    recognition_method: AttendanceMethod


class AttendanceMarkResponse(AttendanceResponse):
    """A mark result. `created` is false when today was already recorded."""

    created: bool


class AttendanceListResponse(BaseModel):
    """One page of attendance rows."""

    items: list[AttendanceResponse]
    total: int
    page: int
    page_size: int


def to_attendance(record: Attendance) -> AttendanceResponse:
    """Build the public attendance row. The student and user must be loaded."""
    student = record.student
    user = student.user
    return AttendanceResponse(
        attendance_id=record.id,
        student_id=record.student_id,
        roll_number=student.roll_number,
        first_name=user.first_name,
        last_name=user.last_name,
        class_id=record.class_id,
        subject_id=record.subject_id,
        teacher_id=record.teacher_id,
        date=record.attendance_date,
        check_in_time=record.marked_at,
        status=record.status,
        confidence_score=_confidence(record.confidence),
        recognition_method=record.method,
    )


def to_attendance_mark(record: Attendance, *, created: bool) -> AttendanceMarkResponse:
    """Build a mark response from a stored row."""
    body = to_attendance(record)
    return AttendanceMarkResponse(**body.model_dump(), created=created)


def _confidence(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)
