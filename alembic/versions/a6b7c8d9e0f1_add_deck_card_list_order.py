"""add deck_cards.list_order (manual order in started list)

Revision ID: a6b7c8d9e0f1
Revises: f5a6b7c8d9e0
Create Date: 2026-07-09 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a6b7c8d9e0f1'
down_revision = 'f5a6b7c8d9e0'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('deck_cards', sa.Column('list_order', sa.Integer(), nullable=True))


def downgrade():
    op.drop_column('deck_cards', 'list_order')
