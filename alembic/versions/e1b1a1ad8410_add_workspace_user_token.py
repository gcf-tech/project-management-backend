"""add workspace_user_token

Revision ID: e1b1a1ad8410
Revises: a3c1d5e7f9b2
Create Date: 2026-08-27 13:05:30.532213

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1b1a1ad8410'
down_revision: Union[str, Sequence[str], None] = 'a3c1d5e7f9b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tablas(bind):
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    """workspace_user_token: access token de Nextcloud cifrado para el push de Talk.
    Idempotente (en prod pudo crearse a mano)."""
    bind = op.get_bind()
    if "workspace_user_token" in _tablas(bind):
        return
    op.create_table(
        "workspace_user_token",
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.Column("access_token_enc", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("talk_seen", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if "workspace_user_token" in _tablas(bind):
        op.drop_table("workspace_user_token")
