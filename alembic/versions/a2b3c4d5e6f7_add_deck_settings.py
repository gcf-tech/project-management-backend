"""add deck_settings (config admin genérica clave→JSON)

Revision ID: a2b3c4d5e6f7
Revises: b7c8d9e0f1a2
Create Date: 2026-07-14 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a2b3c4d5e6f7'
down_revision = 'b7c8d9e0f1a2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'deck_settings',
        sa.Column('key', sa.String(length=80), nullable=False),
        sa.Column('value', sa.JSON(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('key'),
    )


def downgrade():
    op.drop_table('deck_settings')
