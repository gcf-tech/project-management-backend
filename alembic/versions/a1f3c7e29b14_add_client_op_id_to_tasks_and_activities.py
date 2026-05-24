"""add client_op_id to tasks and activities

Revision ID: a1f3c7e29b14
Revises: 5be79ca1ca3b
Create Date: 2026-05-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1f3c7e29b14"
down_revision: Union[str, Sequence[str], None] = "5be79ca1ca3b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add client_op_id idempotency column to tasks and activities."""
    op.add_column(
        "tasks",
        sa.Column("client_op_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_tasks_client_op_id", "tasks", ["client_op_id"], unique=True,
    )

    op.add_column(
        "activities",
        sa.Column("client_op_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_activities_client_op_id", "activities", ["client_op_id"], unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_activities_client_op_id", table_name="activities")
    op.drop_column("activities", "client_op_id")
    op.drop_index("ix_tasks_client_op_id", table_name="tasks")
    op.drop_column("tasks", "client_op_id")
