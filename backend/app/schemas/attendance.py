"""Request and response models for attendance.

A mark stores the lesson, the check-in, and how the student was recognized.
The embedding and the camera frame are not part of the response.
A student's percentage counts present and late as attended. Excused marks
are reported and left out of that rate.
"""

import enum
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, Field

from app.models.attendance import Attendance, AttendanceMethod, AttendanceStatus
from app.models.attendance_session import AttendanceSession, SessionStatus


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


class AttendanceSessionStartRequest(BaseModel):
    """Lesson a teacher is about to take attendance for."""

    class_id: int = Field(ge=1)
    subject_id: int = Field(ge=1)


class AttendanceSessionResponse(BaseModel):
    """One stored attendance session for the signed-in teacher."""

    session_id: int
    class_id: int
    subject_id: int
    teacher_id: int
    date: date
    status: SessionStatus
    started_at: datetime
    ended_at: datetime | None
    created: bool


class AttendanceStatisticsResponse(BaseModel):
    """Status counts for attendance the caller is allowed to read."""

    present: int
    absent: int
    late: int
    excused: int
    total: int


class AttendancePercentageResponse(AttendanceStatisticsResponse):
    """Status counts plus the attended share of counted marks.

    `percentage` is present and late divided by present, absent, and late.
    Excused marks stay in the counts. It is null when nothing is counted.
    """

    percentage: float | None


class AttendanceMonthResponse(AttendancePercentageResponse):
    """One calendar month of the signed-in student's attendance."""

    year: int
    month: int


class CalendarDayStatus(enum.Enum):
    """The single status for a day, or `MIXED` when the day has several."""

    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    LATE = "LATE"
    EXCUSED = "EXCUSED"
    MIXED = "MIXED"


class AttendanceCalendarDayResponse(BaseModel):
    """One date that has at least one mark for this student."""

    date: date
    present: int
    absent: int
    late: int
    excused: int
    total: int
    percentage: float | None
    status: CalendarDayStatus


class AttendanceCalendarResponse(BaseModel):
    """A month grid's marks. Days with no mark are omitted."""

    year: int
    month: int
    present: int
    absent: int
    late: int
    excused: int
    total: int
    percentage: float | None
    days: list[AttendanceCalendarDayResponse]


class SubjectAttendanceResponse(BaseModel):
    """Attendance totals for one subject this student can see."""

    subject_id: int
    class_id: int
    name: str
    code: str
    present: int
    absent: int
    late: int
    excused: int
    total: int
    percentage: float | None


class SubjectAttendanceListResponse(BaseModel):
    """Per-subject attendance for the signed-in student."""

    items: list[SubjectAttendanceResponse]


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


def to_attendance_session(record: AttendanceSession, *, created: bool) -> AttendanceSessionResponse:
    """Build the public session. The row must already be stored."""
    return AttendanceSessionResponse(
        session_id=record.id,
        class_id=record.class_id,
        subject_id=record.subject_id,
        teacher_id=record.teacher_id,
        date=record.session_date,
        status=record.status,
        started_at=record.started_at,
        ended_at=record.ended_at,
        created=created,
    )


def to_statistics(counts: dict[AttendanceStatus, int]) -> AttendanceStatisticsResponse:
    """Build status totals. Missing statuses count as zero."""
    present, absent, late, excused = _parts(counts)
    return AttendanceStatisticsResponse(
        present=present,
        absent=absent,
        late=late,
        excused=excused,
        total=present + absent + late + excused,
    )


def attendance_percentage(*, present: int, absent: int, late: int) -> float | None:
    """Return the attended share of counted marks, to one decimal place.

    Present and late count as attended. Excused is not part of this rate.
    """
    counted = present + absent + late
    if counted == 0:
        return None
    rate = (Decimal(present + late) * Decimal(100) / Decimal(counted)).quantize(
        Decimal("0.1"),
        rounding=ROUND_HALF_UP,
    )
    return float(rate)


def to_percentage(counts: dict[AttendanceStatus, int]) -> AttendancePercentageResponse:
    """Build status totals and the attendance percentage."""
    stats = to_statistics(counts)
    return AttendancePercentageResponse(
        **stats.model_dump(),
        percentage=attendance_percentage(present=stats.present, absent=stats.absent, late=stats.late),
    )


def to_month(counts: dict[AttendanceStatus, int], *, year: int, month: int) -> AttendanceMonthResponse:
    """Build one month of totals for the signed-in student."""
    body = to_percentage(counts)
    return AttendanceMonthResponse(**body.model_dump(), year=year, month=month)


def to_calendar_day(day: date, counts: dict[AttendanceStatus, int]) -> AttendanceCalendarDayResponse:
    """Build one calendar date from that date's status counts."""
    stats = to_percentage(counts)
    return AttendanceCalendarDayResponse(
        date=day,
        present=stats.present,
        absent=stats.absent,
        late=stats.late,
        excused=stats.excused,
        total=stats.total,
        percentage=stats.percentage,
        status=_day_status(stats.present, stats.absent, stats.late, stats.excused),
    )


def to_subject_attendance(
    *,
    subject_id: int,
    class_id: int,
    name: str,
    code: str,
    counts: dict[AttendanceStatus, int],
) -> SubjectAttendanceResponse:
    """Build one subject's totals for the signed-in student."""
    stats = to_percentage(counts)
    return SubjectAttendanceResponse(
        subject_id=subject_id,
        class_id=class_id,
        name=name,
        code=code,
        present=stats.present,
        absent=stats.absent,
        late=stats.late,
        excused=stats.excused,
        total=stats.total,
        percentage=stats.percentage,
    )


def _parts(counts: dict[AttendanceStatus, int]) -> tuple[int, int, int, int]:
    return (
        counts.get(AttendanceStatus.PRESENT, 0),
        counts.get(AttendanceStatus.ABSENT, 0),
        counts.get(AttendanceStatus.LATE, 0),
        counts.get(AttendanceStatus.EXCUSED, 0),
    )


def _day_status(present: int, absent: int, late: int, excused: int) -> CalendarDayStatus:
    found: list[CalendarDayStatus] = []
    if present:
        found.append(CalendarDayStatus.PRESENT)
    if absent:
        found.append(CalendarDayStatus.ABSENT)
    if late:
        found.append(CalendarDayStatus.LATE)
    if excused:
        found.append(CalendarDayStatus.EXCUSED)
    if len(found) == 1:
        return found[0]
    return CalendarDayStatus.MIXED


def _confidence(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)
