"""add deck_cards.status (estado de tarea: not_started/in_progress/paused/done/cancelled)

Revision ID: d0e1f2a3b4c5
Revises: c3ce7a92f57a
Create Date: 2026-08-06 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'd0e1f2a3b4c5'
down_revision = 'c3ce7a92f57a'
branch_labels = None
depends_on = None


def _has_col(bind, table, col):
    insp = sa.inspect(bind)
    return any(c['name'] == col for c in insp.get_columns(table))


def upgrade():
    bind = op.get_bind()
    if not _has_col(bind, 'deck_cards', 'status'):
        op.add_column('deck_cards', sa.Column(
            'status', sa.String(length=20), nullable=False, server_default='not_started'))
    # Sembrar coherencia: las ya completadas → 'done'.
    op.execute("UPDATE deck_cards SET status='done' WHERE completed_at IS NOT NULL AND status='not_started'")


def downgrade():
    bind = op.get_bind()
    if _has_col(bind, 'deck_cards', 'status'):
        op.drop_column('deck_cards', 'status')
