"""enforce timezone aware UTC datetimes

Revision ID: 5be79ca1ca3b
Revises: 
Create Date: 2026-05-01 09:43:06.204021

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5be79ca1ca3b'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    for table_name, column_name in _DATETIME_COLUMNS:
        op.alter_column(
            table_name,
            column_name,
            existing_type=sa.DateTime(),
            type_=sa.DateTime(timezone=True),
            existing_nullable=True,
        )


def downgrade() -> None:
    """Downgrade schema."""
    for table_name, column_name in _DATETIME_COLUMNS:
        op.alter_column(
            table_name,
            column_name,
            existing_type=sa.DateTime(timezone=True),
            type_=sa.DateTime(),
            existing_nullable=True,
        )


_DATETIME_COLUMNS: list[tuple[str, str]] = [
    ("teams", "created_at"),
    ("teams", "updated_at"),
    ("users", "created_at"),
    ("users", "updated_at"),
    ("tasks", "completed_at"),
    ("tasks", "created_at"),
    ("tasks", "updated_at"),
    ("tasks", "deleted_at"),
    ("activities", "completed_at"),
    ("activities", "created_at"),
    ("activities", "updated_at"),
    ("activities", "deleted_at"),
    ("subtasks", "created_at"),
    ("time_logs", "start_at"),
    ("time_logs", "created_at"),
    ("time_logs", "updated_at"),
    ("observations", "created_at"),
    ("skills", "created_at"),
    ("user_skills", "updated_at"),
    ("skill_endorsements", "created_at"),
    ("user_preferences", "updated_at"),
    ("weekly_blocks", "dtstart"),
    ("weekly_blocks", "rrule_until"),
    ("weekly_blocks", "created_at"),
    ("weekly_blocks", "updated_at"),
]
