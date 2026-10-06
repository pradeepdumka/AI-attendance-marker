"""Login identity for every person who uses the system.

A user may also be a student, a teacher, or neither (an admin). The link is
optional on this side: a profile row points at exactly one user. Passwords
are stored only as `password_hash`. This module never accepts or persists
a plain-text password.
"""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, enum_column

if TYPE_CHECKING:
    from app.models.attendance import Attendance
    from app.models.audit_log import AuditLog
    from app.models.student import Student
    from app.models.teacher import Teacher


class UserRole(enum.Enum):
    """Who the account is allowed to act as."""

    ADMIN = "ADMIN"
    TEACHER = "TEACHER"
    STUDENT = "STUDENT"


class User(TimestampMixin, Base):
    """Authentication and display identity. Not a class roster row."""

    __tablename__ = "users"
    __table_args__ = (Index("ix_users_email", "email", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        enum_column(UserRole, name="user_role"),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("1"),
    )

    student: Mapped[Student | None] = relationship(
        back_populates="user",
        uselist=False,
    )
    teacher: Mapped[Teacher | None] = relationship(
        back_populates="user",
        uselist=False,
    )
    marked_attendance: Mapped[list[Attendance]] = relationship(
        back_populates="marked_by",
    )
    audit_logs: Mapped[list[AuditLog]] = relationship(
        back_populates="actor",
    )

    def __repr__(self) -> str:
        return f"User(id={self.id!r}, email={self.email!r}, role={self.role!r})"
