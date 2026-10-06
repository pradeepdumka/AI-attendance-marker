"""Numeric face embedding for one student.

The column stores the embedding produced by a later recognition phase
(a list of floats), not an image and not a password. A student may have
several encodings. Deleting the student deletes those rows.
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
