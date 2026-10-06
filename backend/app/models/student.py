"""Student profile attached to one user account.

The user side of this link is optional. The student side is not: a roster
row always belongs to exactly one user, and that user has at most one
student profile. Name, email, phone, and whether the account can sign in
live on the user. This table stores the academic identity.
"""

from __future__ import annotations

import enum
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, enum_column

if TYPE_CHECKING:
    from app.models.attendance import Attendance
    from app.models.enrollment import Enrollment
    from app.models.face_encoding import FaceEncoding
    from app.models.user import User


class Gender(enum.Enum):
    """Gender recorded on a student profile. Unknown stays null."""

    FEMALE = "FEMALE"
    MALE = "MALE"
    OTHER = "OTHER"


class Student(TimestampMixin, Base):
    """Academic identity used by enrollment, attendance, and face data."""

    __tablename__ = "students"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
    roll_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[Gender | None] = mapped_column(
        enum_column(Gender, name="student_gender"),
        nullable=True,
    )

    user: Mapped[User] = relationship(back_populates="student")
    enrollments: Mapped[list[Enrollment]] = relationship(back_populates="student")
    face_encodings: Mapped[list[FaceEncoding]] = relationship(
        back_populates="student",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    attendance_records: Mapped[list[Attendance]] = relationship(
        back_populates="student",
    )

    def __repr__(self) -> str:
        return f"Student(id={self.id!r}, roll_number={self.roll_number!r})"
