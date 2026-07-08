"""add deck_cards.parent_card_id (subtasks)

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-07-08 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'f5a6b7c8d9e0'
down_revision = 'e4f5a6b7c8d9'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('deck_cards', sa.Column('parent_card_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_deck_cards_parent', 'deck_cards', 'deck_cards',
        ['parent_card_id'], ['id'], ondelete='SET NULL',
    )
    op.create_index('idx_deck_cards_parent', 'deck_cards', ['parent_card_id'])


def downgrade():
    op.drop_index('idx_deck_cards_parent', table_name='deck_cards')
    op.drop_constraint('fk_deck_cards_parent', 'deck_cards', type_='foreignkey')
    op.drop_column('deck_cards', 'parent_card_id')
