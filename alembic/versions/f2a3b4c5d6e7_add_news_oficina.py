"""add oficina to workspace_news (news por oficina)

NOTA: rebasada sobre 'a1b2c3d4e5f6' (revisión real de la BD de producción; esta copia
local del backend está desincronizada y no contiene ese archivo). El upgrade es
idempotente: si la columna ya existe (se agregó a mano por SQL), solo avanza la versión.

Revision ID: f2a3b4c5d6e7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-18 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(bind):
    insp = sa.inspect(bind)
    return {c["name"] for c in insp.get_columns("workspace_news")}


def upgrade() -> None:
    bind = op.get_bind()
    if "oficina" not in _cols(bind):
        op.add_column('workspace_news', sa.Column('oficina', sa.String(30), nullable=True))
        op.create_index('idx_workspace_news_oficina', 'workspace_news', ['oficina'])


def downgrade() -> None:
    bind = op.get_bind()
    if "oficina" in _cols(bind):
        try:
            op.drop_index('idx_workspace_news_oficina', table_name='workspace_news')
        except Exception:
            pass
        op.drop_column('workspace_news', 'oficina')
