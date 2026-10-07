"""add attendance sessions

Revision ID: c3a91e7b2d04
Revises: b7e4c1a09d52
Create Date: 2026-10-06 16:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.models.base import UtcDateTime


revision: str = "c3a91e7b2d04"
down_revision: Union[str, Sequence[str], None] = "b7e4c1a09d52"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Store one attendance session per teacher, class, subject, and date."""
    op.create_table(
        "attendance_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("teacher_id", sa.Integer(), nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("OPEN", "CLOSED", name="attendance_session_status"),
            nullable=False,
        ),
        sa.Column("started_at", UtcDateTime(), nullable=False),
        sa.Column("ended_at", UtcDateTime(), nullable=True),
        sa.Column(
            "created_at",
            UtcDateTime(),
            server_default=sa.text("(UTC_TIMESTAMP())"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            UtcDateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["class_id"],
            ["classes.id"],
            name=op.f("fk_attendance_sessions_class_id_classes"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["subject_id"],
            ["subjects.id"],
            name=op.f("fk_attendance_sessions_subject_id_subjects"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["teacher_id"],
            ["teachers.id"],
            name=op.f("fk_attendance_sessions_teacher_id_teachers"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_attendance_sessions")),
        sa.UniqueConstraint(
            "teacher_id",
            "class_id",
            "subject_id",
            "session_date",
            name="uq_attendance_sessions_lesson_date",
        ),
    )
    op.create_index(
        op.f("ix_attendance_sessions_teacher_id"),
        "attendance_sessions",
        ["teacher_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove attendance sessions."""
    op.drop_index(op.f("ix_attendance_sessions_teacher_id"), table_name="attendance_sessions")
    op.drop_table("attendance_sessions")
