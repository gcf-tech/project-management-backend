"""add_role_commercial_column

Revision ID: d2e4f5a6b7c8
Revises: c1b9d4e2a3f5
Create Date: 2026-06-22 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd2e4f5a6b7c8'
down_revision = 'c1b9d4e2a3f5'
branch_labels = None
depends_on = None


def upgrade():
    # Add role_commercial column
    op.add_column('users', sa.Column('role_commercial', sa.String(50), nullable=True))
    
    # Set initial values based on team_id
    # team_id = 7 → admin
    # team_id = 2 → commercial
    op.execute("""
        UPDATE users 
        SET role_commercial = CASE 
            WHEN team_id = 7 THEN 'admin'
            WHEN team_id = 2 THEN 'commercial'
            ELSE 'member'
        END
    """)


def downgrade():
    op.drop_column('users', 'role_commercial')
