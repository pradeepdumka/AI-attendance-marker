"""Numeric face embedding for one student.

Enrollment stores one row per captured sample: a list of 128 floats,
not the image. `source_image_path` stays empty. A student may have
several active samples. Deleting the student deletes those rows.
`app.services.face_recognition` compares a camera frame with the active
rows. That comparison does not write an attendance record.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin

if TYPE_CHECKING:
    from app.models.student import Student


class FaceEncoding(CreatedAtMixin, Base):
    """One stored embedding used to match a camera frame to a student."""

    __tablename__ = "face_encodings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    encoding: Mapped[list[float]] = mapped_column(JSON, nullable=False)
    source_image_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("1"),
    )

    student: Mapped[Student] = relationship(back_populates="face_encodings")

    def __repr__(self) -> str:
        return (
            f"FaceEncoding(id={self.id!r}, student_id={self.student_id!r}, "
            f"is_active={self.is_active!r})"
        )
