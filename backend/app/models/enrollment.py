"""Membership of a student in a class for a period of time.

A student can be enrolled in more than one class across years. The same
pair cannot be inserted twice; the status column records whether that
membership is still current.
"""

from __future__ import annotations

import enum
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, enum_column, utcnow

if TYPE_CHECKING:
    from app.models.class_model import SchoolClass
    from app.models.student import Student


class EnrollmentStatus(enum.Enum):
    """Whether the student is currently in the class."""

    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    WITHDRAWN = "WITHDRAWN"


def _utc_today() -> date:
    return utcnow().date()


class Enrollment(TimestampMixin, Base):
    """Join row between a student and a class."""

    __tablename__ = "enrollments"
    __table_args__ = (
        UniqueConstraint(
            "student_id",
            "class_id",
            name="uq_enrollments_student_id_class_id",
        ),
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
    status: Mapped[EnrollmentStatus] = mapped_column(
        enum_column(EnrollmentStatus, name="enrollment_status"),
        nullable=False,
        default=EnrollmentStatus.ACTIVE,
        server_default=EnrollmentStatus.ACTIVE.value,
    )
    enrolled_on: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        default=_utc_today,
    )

    student: Mapped[Student] = relationship(back_populates="enrollments")
    school_class: Mapped[SchoolClass] = relationship(back_populates="enrollments")

    def __repr__(self) -> str:
        return (
            f"Enrollment(id={self.id!r}, student_id={self.student_id!r}, "
            f"class_id={self.class_id!r}, status={self.status!r})"
        )
