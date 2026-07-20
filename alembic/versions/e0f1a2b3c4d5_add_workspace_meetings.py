"""add workspace meetings (+ merge de los dos heads)

Fusiona las ramas d9e0f1a2b3c4 (workspace tables) y a2b3c4d5e6f7 (deck_settings) en
un solo head, y agrega las tablas de reuniones del workspace.

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4, a2b3c4d5e6f7
Create Date: 2026-07-20 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e0f1a2b3c4d5'
down_revision: Union[str, Sequence[str], None] = ('d9e0f1a2b3c4', 'a2b3c4d5e6f7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'workspace_meetings',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('titulo', sa.String(255), nullable=True),
        sa.Column('meet_url', sa.String(500), nullable=True),
        sa.Column('inicio', sa.DateTime(timezone=True), nullable=False),
        sa.Column('fin', sa.DateTime(timezone=True), nullable=True),
        sa.Column('creador_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['creador_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('idx_workspace_meetings_inicio', 'inicio'),
    )
    op.create_table(
        'workspace_meeting_participants',
        sa.Column('meeting_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['meeting_id'], ['workspace_meetings.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('meeting_id', 'user_id'),
        sa.Index('idx_wmp_user', 'user_id'),
    )


def downgrade() -> None:
    op.drop_table('workspace_meeting_participants')
    op.drop_table('workspace_meetings')
