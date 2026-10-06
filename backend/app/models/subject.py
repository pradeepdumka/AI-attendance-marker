"""A subject taught to one class, optionally by one teacher.

`code` is unique inside a class, so two sections can both offer MATH
without sharing a row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.attendance import Attendance
    from app.models.class_model import SchoolClass
    from app.models.teacher import Teacher


class Subject(TimestampMixin, Base):
    """Course offering that attendance is marked against."""

    __tablename__ = "subjects"
    __table_args__ = (
        UniqueConstraint("class_id", "code", name="uq_subjects_class_id_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    class_id: Mapped[int] = mapped_column(
        ForeignKey("classes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    teacher_id: Mapped[int | None] = mapped_column(
        ForeignKey("teachers.id", ondelete="RESTRICT"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("1"),
    )

    school_class: Mapped[SchoolClass] = relationship(back_populates="subjects")
    teacher: Mapped[Teacher | None] = relationship(back_populates="subjects")
    attendance_records: Mapped[list[Attendance]] = relationship(
        back_populates="subject",
    )

    def __repr__(self) -> str:
        return f"Subject(id={self.id!r}, code={self.code!r}, name={self.name!r})"
