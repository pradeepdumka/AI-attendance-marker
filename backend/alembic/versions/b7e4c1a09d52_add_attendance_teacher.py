"""add attendance teacher

Revision ID: b7e4c1a09d52
Revises: 4f8c2e1a9b63
Create Date: 2026-10-06 10:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b7e4c1a09d52"
down_revision: Union[str, Sequence[str], None] = "4f8c2e1a9b63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Store the lesson teacher on each attendance row."""
    op.add_column(
        "attendance_records",
        sa.Column("teacher_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        op.f("ix_attendance_records_teacher_id"),
        "attendance_records",
        ["teacher_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_attendance_records_teacher_id_teachers"),
        "attendance_records",
        "teachers",
        ["teacher_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """Remove the lesson teacher from attendance rows."""
    op.drop_constraint(
        op.f("fk_attendance_records_teacher_id_teachers"),
        "attendance_records",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_attendance_records_teacher_id"), table_name="attendance_records")
    op.drop_column("attendance_records", "teacher_id")
