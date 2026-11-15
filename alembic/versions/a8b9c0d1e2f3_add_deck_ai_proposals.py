"""add deck_ai_proposals + deck_ai_proposal_events (planificación asistida por IA)

Solo AGREGA dos tablas nuevas; no altera ninguna tabla existente. Idempotente:
si las tablas ya existen no hace nada (misma convención que las migraciones
previas del Deck).

Revision ID: a8b9c0d1e2f3
Revises: e1b1a1ad8410
Create Date: 2026-11-11 10:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a8b9c0d1e2f3'
down_revision: Union[str, Sequence[str], None] = 'e1b1a1ad8410'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tablas(bind):
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    existentes = _tablas(bind)

    if 'deck_ai_proposals' not in existentes:
        op.create_table(
            'deck_ai_proposals',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('board_id', sa.Integer(), sa.ForeignKey('deck_boards.id', ondelete='CASCADE'), nullable=False),
            sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
            sa.Column('input_text', sa.Text(), nullable=False),
            sa.Column('input_kind', sa.String(10), nullable=False, server_default='text'),
            sa.Column('context_mode', sa.String(10), nullable=False, server_default='full'),
            sa.Column('model', sa.String(80), nullable=True),
            sa.Column('status', sa.String(20), nullable=False, server_default='draft'),
            sa.Column('payload_ai', sa.JSON(), nullable=False),
            sa.Column('payload_final', sa.JSON(), nullable=True),
            sa.Column('context_snapshot', sa.JSON(), nullable=True),
            sa.Column('validation_warnings', sa.JSON(), nullable=True),
            sa.Column('prompt_tokens', sa.Integer(), nullable=True),
            sa.Column('completion_tokens', sa.Integer(), nullable=True),
            sa.Column('regenerations', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('manual_edits', sa.JSON(), nullable=True),
            sa.Column('parent_card_id', sa.Integer(), sa.ForeignKey('deck_cards.id', ondelete='SET NULL'), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
            mysql_charset='utf8mb4',
        )
        op.create_index('idx_deck_ai_prop_board', 'deck_ai_proposals', ['board_id'])
        op.create_index('idx_deck_ai_prop_status', 'deck_ai_proposals', ['status'])

    if 'deck_ai_proposal_events' not in existentes:
        op.create_table(
            'deck_ai_proposal_events',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('proposal_id', sa.Integer(), sa.ForeignKey('deck_ai_proposals.id', ondelete='CASCADE'), nullable=False),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
            sa.Column('event', sa.String(20), nullable=False),
            sa.Column('from_status', sa.String(20), nullable=True),
            sa.Column('to_status', sa.String(20), nullable=True),
            sa.Column('feedback', sa.Text(), nullable=True),
            sa.Column('detail', sa.JSON(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
            mysql_charset='utf8mb4',
        )
        op.create_index('idx_deck_ai_evt_prop', 'deck_ai_proposal_events', ['proposal_id'])


def downgrade() -> None:
    bind = op.get_bind()
    existentes = _tablas(bind)
    if 'deck_ai_proposal_events' in existentes:
        op.drop_table('deck_ai_proposal_events')
    if 'deck_ai_proposals' in existentes:
        op.drop_table('deck_ai_proposals')
