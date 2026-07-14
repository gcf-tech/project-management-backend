"""add workspace_manager column

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-07-13 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c8d9e0f1a2b3'
down_revision = 'b7c8d9e0f1a2'
branch_labels = None
depends_on = None


def upgrade():
    # Manual per-app role flag for the workspace (oficina virtual). NOT synced from
    # Nextcloud. Seeded from Supabase `perfiles.es_gerente` by the one-off data
    # migration (matched by email); left 0 here.
    op.add_column(
        'users',
        sa.Column('workspace_manager', sa.Boolean(), nullable=False, server_default='0'),
    )


def downgrade():
    op.drop_column('users', 'workspace_manager')
