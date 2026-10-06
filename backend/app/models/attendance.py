"""One attendance mark for a student in a subject on a calendar date.

A student has a single row per subject per date. Corrections update that
row. `marked_by_id` is the user who recorded it and may be null when a
later recognition job writes the row without a logged-in teacher.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UtcDateTime, enum_column, utcnow

if TYPE_CHECKING:
    from app.models.class_model import SchoolClass
    from app.models.student import Student
    from app.models.subject import Subject
    from app.models.user import User


class AttendanceStatus(enum.Enum):
    """Result recorded for that student on that date."""

    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    LATE = "LATE"
    EXCUSED = "EXCUSED"


class AttendanceMethod(enum.Enum):
    """How the mark was produced."""

    MANUAL = "MANUAL"
    FACE_RECOGNITION = "FACE_RECOGNITION"


class Attendance(TimestampMixin, Base):
    """Daily subject attendance. Face matching confidence is optional."""

    __tablename__ = "attendance_records"
    __table_args__ = (
        UniqueConstraint(
            "student_id",
            "subject_id",
            "attendance_date",
            name="uq_attendance_student_subject_date",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="confidence_range",
        ),
        Index("ix_attendance_records_class_date", "class_id", "attendance_date"),
        Index("ix_attendance_records_attendance_date", "attendance_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="RESTRICT"),
        nullable=False,
    )
    class_id: Mapped[int] = mapped_column(
        ForeignKey("classes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subjects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    attendance_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[AttendanceStatus] = mapped_column(
        enum_column(AttendanceStatus, name="attendance_status"),
        nullable=False,
    )
    method: Mapped[AttendanceMethod] = mapped_column(
        enum_column(AttendanceMethod, name="attendance_method"),
        nullable=False,
    )
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    marked_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    marked_at: Mapped[datetime] = mapped_column(
        UtcDateTime(),
        nullable=False,
        default=utcnow,
        server_default=text("(UTC_TIMESTAMP())"),
    )
    remarks: Mapped[str | None] = mapped_column(String(500), nullable=True)

    student: Mapped[Student] = relationship(back_populates="attendance_records")
    school_class: Mapped[SchoolClass] = relationship(back_populates="attendance_records")
    subject: Mapped[Subject] = relationship(back_populates="attendance_records")
    marked_by: Mapped[User | None] = relationship(back_populates="marked_attendance")

    def __repr__(self) -> str:
        return (
            f"Attendance(id={self.id!r}, student_id={self.student_id!r}, "
            f"attendance_date={self.attendance_date!r}, status={self.status!r})"
        )
