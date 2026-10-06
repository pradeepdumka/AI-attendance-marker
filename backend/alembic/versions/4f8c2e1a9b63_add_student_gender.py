"""add student gender

Revision ID: 4f8c2e1a9b63
Revises: 0fcfcf13dcce
Create Date: 2026-10-05 20:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "4f8c2e1a9b63"
down_revision: Union[str, Sequence[str], None] = "0fcfcf13dcce"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add an optional gender to the student profile."""
    op.add_column(
        "students",
        sa.Column(
            "gender",
            sa.Enum("FEMALE", "MALE", "OTHER", name="student_gender"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Remove student gender."""
    op.drop_column("students", "gender")
