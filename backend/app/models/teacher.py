"""Teacher profile attached to one user account.

Same cardinality as Student: optional from User, required and unique from
this side. Name, email, phone, and whether the account can sign in live
on the user. `employee_code` is the unique employee id.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.class_model import SchoolClass
    from app.models.subject import Subject
    from app.models.user import User


class Teacher(TimestampMixin, Base):
    """Staff identity used when assigning a class or a subject."""

    __tablename__ = "teachers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
    # Unique employee id. The API calls this employee_id.
    employee_code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    department: Mapped[str | None] = mapped_column(String(100), nullable=True)

    user: Mapped[User] = relationship(back_populates="teacher")
    homeroom_classes: Mapped[list[SchoolClass]] = relationship(
        back_populates="class_teacher",
    )
    subjects: Mapped[list[Subject]] = relationship(back_populates="teacher")

    def __repr__(self) -> str:
        return f"Teacher(id={self.id!r}, employee_code={self.employee_code!r})"
