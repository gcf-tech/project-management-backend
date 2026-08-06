"""add users.notify_email (notificaciones por correo on/off, default on)

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f3
Create Date: 2026-08-06 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'b8c9d0e1f2a3'
down_revision = 'a7b8c9d0e1f3'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('notify_email', sa.Boolean(), nullable=False, server_default='1'))


def downgrade():
    op.drop_column('users', 'notify_email')
