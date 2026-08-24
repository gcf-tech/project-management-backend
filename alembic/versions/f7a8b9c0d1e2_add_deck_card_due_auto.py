"""add deck_cards.due_auto (fecha de vencimiento derivada de subtareas) + merge heads

Unifica las 5 cabezas vigentes en un solo head y agrega la columna due_auto.
due_auto=True marca que la fecha de vencimiento fue derivada automáticamente de la
subtarea más lejana (por no tener fecha manual). Idempotente.

Revision ID: f7a8b9c0d1e2
Revises: a2b3c4d5e6f7, c3d4e5f6a7b8, c8d9e0f1a2b3, d4e5f6a7b8c9, e1f2a3b4c5d6, f2a3b4c5d6e7
Create Date: 2026-08-24 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'f7a8b9c0d1e2'
down_revision = ('a2b3c4d5e6f7', 'c3d4e5f6a7b8', 'c8d9e0f1a2b3', 'd4e5f6a7b8c9', 'e1f2a3b4c5d6', 'f2a3b4c5d6e7')
branch_labels = None
depends_on = None


def _has_col(bind, table, col):
    insp = sa.inspect(bind)
    return any(c['name'] == col for c in insp.get_columns(table))


def upgrade():
    bind = op.get_bind()
    if not _has_col(bind, 'deck_cards', 'due_auto'):
        op.add_column('deck_cards', sa.Column(
            'due_auto', sa.Boolean(), nullable=False, server_default='0'))


def downgrade():
    bind = op.get_bind()
    if _has_col(bind, 'deck_cards', 'due_auto'):
        op.drop_column('deck_cards', 'due_auto')
