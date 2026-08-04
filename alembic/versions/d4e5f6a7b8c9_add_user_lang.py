"""add users.lang (idioma preferido del usuario: es/en)

Revision ID: d4e5f6a7b8c9
Revises: f6a7b8c9d0e1
Create Date: 2026-08-04 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'd4e5f6a7b8c9'
down_revision = 'f6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('lang', sa.String(length=5), nullable=True))


def downgrade():
    op.drop_column('users', 'lang')
