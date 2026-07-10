"""add deck_card_favorites (tareas favoritas por usuario)

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
Create Date: 2026-07-10 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'b7c8d9e0f1a2'
down_revision = 'a6b7c8d9e0f1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'deck_card_favorites',
        sa.Column('card_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['card_id'], ['deck_cards.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('card_id', 'user_id'),
    )
    op.create_index('idx_deck_fav_user', 'deck_card_favorites', ['user_id'])


def downgrade():
    op.drop_index('idx_deck_fav_user', table_name='deck_card_favorites')
    op.drop_table('deck_card_favorites')
