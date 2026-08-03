"""add workspace news (tablero de novedades del Holding)

Revision ID: f6a7b8c9d0e1
Revises: c3d4e5f6a7b8
Create Date: 2026-08-03 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'workspace_news',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('tipo', sa.String(30), nullable=False, server_default='nota'),
        sa.Column('titulo', sa.String(255), nullable=True),
        sa.Column('cuerpo', sa.Text(), nullable=False),
        sa.Column('autor_id', sa.Integer(), nullable=True),
        sa.Column('fijado', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['autor_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_workspace_news_created', 'workspace_news', ['created_at'])


def downgrade() -> None:
    op.drop_index('idx_workspace_news_created', table_name='workspace_news')
    op.drop_table('workspace_news')
