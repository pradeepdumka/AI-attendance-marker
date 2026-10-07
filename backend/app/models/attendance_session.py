"""One attendance session for a teacher, class, and subject on a date.

Starting a session opens that lesson for face recognition and manual
marks. The same lesson on the same date reopens the existing row instead
of inserting another. Closing it records when the teacher stopped.
"""

from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UtcDateTime, enum_column, utcnow


class SessionStatus(enum.Enum):
    """Whether the teacher is currently taking this lesson's attendance."""

    OPEN = "OPEN"
    CLOSED = "CLOSED"


class AttendanceSession(TimestampMixin, Base):
    """A teacher's open or closed attendance take for one lesson and date."""

    __tablename__ = "attendance_sessions"
    __table_args__ = (
        UniqueConstraint(
            "teacher_id",
            "class_id",
            "subject_id",
            "session_date",
            name="uq_attendance_sessions_lesson_date",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    teacher_id: Mapped[int] = mapped_column(
        ForeignKey("teachers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    class_id: Mapped[int] = mapped_column(
        ForeignKey("classes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subjects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[SessionStatus] = mapped_column(
        enum_column(SessionStatus, name="attendance_session_status"),
        nullable=False,
        default=SessionStatus.OPEN,
    )
    started_at: Mapped[datetime] = mapped_column(
        UtcDateTime(),
        nullable=False,
        default=utcnow,
    )
    ended_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)

    teacher = relationship("Teacher")
    school_class = relationship("SchoolClass")
    subject = relationship("Subject")

    def __repr__(self) -> str:
        return (
            f"AttendanceSession(id={self.id!r}, teacher_id={self.teacher_id!r}, "
            f"class_id={self.class_id!r}, subject_id={self.subject_id!r}, "
            f"session_date={self.session_date!r}, status={self.status!r})"
        )
