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


def _has_col(bind, table, col):
    insp = sa.inspect(bind)
    return any(c['name'] == col for c in insp.get_columns(table))


def upgrade():
    bind = op.get_bind()
    # Idempotente: en prod la columna pudo agregarse por SQL directo (alembic
    # quedó desincronizado con la cadena desplegada).
    if not _has_col(bind, 'users', 'notify_email'):
        op.add_column('users', sa.Column('notify_email', sa.Boolean(), nullable=False, server_default='1'))


def downgrade():
    bind = op.get_bind()
    if _has_col(bind, 'users', 'notify_email'):
        op.drop_column('users', 'notify_email')
