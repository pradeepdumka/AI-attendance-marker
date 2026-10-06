"""A homeroom group, such as Grade 10 section A for one academic year.

The module is `class_model` because `class` is a Python keyword. The table
name is `classes`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.attendance import Attendance
    from app.models.enrollment import Enrollment
    from app.models.subject import Subject
    from app.models.teacher import Teacher


class SchoolClass(TimestampMixin, Base):
    """One section of students taught together for an academic year."""

    __tablename__ = "classes"
    __table_args__ = (
        UniqueConstraint(
            "name",
            "section",
            "academic_year",
            name="uq_classes_name_section_year",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    section: Mapped[str] = mapped_column(String(20), nullable=False)
    academic_year: Mapped[str] = mapped_column(String(20), nullable=False)
    class_teacher_id: Mapped[int | None] = mapped_column(
        ForeignKey("teachers.id", ondelete="RESTRICT"),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("1"),
    )

    class_teacher: Mapped[Teacher | None] = relationship(
        back_populates="homeroom_classes",
    )
    subjects: Mapped[list[Subject]] = relationship(back_populates="school_class")
    enrollments: Mapped[list[Enrollment]] = relationship(back_populates="school_class")
    attendance_records: Mapped[list[Attendance]] = relationship(
        back_populates="school_class",
    )

    def __repr__(self) -> str:
        return (
            f"SchoolClass(id={self.id!r}, name={self.name!r}, "
            f"section={self.section!r}, academic_year={self.academic_year!r})"
        )
