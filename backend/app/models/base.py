"""Shared SQLAlchemy base, UTC timestamps, and enum columns.

Every ORM class subclasses `Base`, so Alembic sees one metadata registry.
Timestamps are stored as MySQL `DATETIME(6)` in UTC. The session time zone
is pinned to UTC on connect, and `UTC_TIMESTAMP()` does not follow the
session zone, so a non-ORM insert still records UTC.
"""

import enum
from datetime import datetime, timezone

from sqlalchemy import DateTime, MetaData, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


class UtcDateTime(TypeDecorator[datetime]):
    """MySQL DATETIME that always binds and loads UTC.

    MySQL does not store a time zone on DATETIME. Values are converted to
    UTC before insert, and loaded values are tagged with UTC so application
    code does not treat them as local time.
    """

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect):  # type: ignore[override]
        if dialect.name == "mysql":
            from sqlalchemy.dialects.mysql import DATETIME

            return dialect.type_descriptor(DATETIME(fsp=6))
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(  # type: ignore[override]
        self,
        value: datetime | None,
        dialect: Dialect,
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("timestamps must be timezone-aware UTC datetimes")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(  # type: ignore[override]
        self,
        value: datetime | None,
        dialect: Dialect,
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    """Registry for every application table."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class CreatedAtMixin:
    """Row creation time. Immutable from the application's point of view."""

    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime(),
        nullable=False,
        default=utcnow,
        server_default=text("(UTC_TIMESTAMP())"),
        sort_order=100,
    )


class TimestampMixin(CreatedAtMixin):
    """Creation and modification times, both in UTC."""

    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime(),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=text("(UTC_TIMESTAMP()) ON UPDATE CURRENT_TIMESTAMP"),
        sort_order=101,
    )


def enum_column(enum_cls: type[enum.Enum], *, name: str) -> SAEnum:
    """MySQL ENUM that stores the Python enum values, not the member names."""
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=True,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )

