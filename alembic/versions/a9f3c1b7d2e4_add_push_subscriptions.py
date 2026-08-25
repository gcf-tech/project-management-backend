"""add workspace_push_subscription (Web Push de la PWA)

Idempotente: si la tabla ya existe, solo avanza la versión.

Revision ID: a9f3c1b7d2e4
Revises: f7a8b9c0d1e2
Create Date: 2026-08-25 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a9f3c1b7d2e4'
down_revision: Union[str, Sequence[str], None] = 'f7a8b9c0d1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables(bind):
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    if "workspace_push_subscription" in _tables(bind):
        return
    op.create_table(
        "workspace_push_subscription",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("endpoint", sa.String(length=500), nullable=False),
        sa.Column("p256dh", sa.String(length=255), nullable=False),
        sa.Column("auth", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("endpoint", name="uq_push_endpoint"),
    )
    op.create_index("idx_push_user", "workspace_push_subscription", ["user_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if "workspace_push_subscription" not in _tables(bind):
        return
    try:
        op.drop_index("idx_push_user", table_name="workspace_push_subscription")
    except Exception:
        pass
    op.drop_table("workspace_push_subscription")
