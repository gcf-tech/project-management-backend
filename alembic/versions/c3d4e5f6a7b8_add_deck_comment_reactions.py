"""add deck_comment_reactions (reacciones emoji a comentarios)

Revision ID: c3d4e5f6a7b8
Revises: e0f1a2b3c4d5
Create Date: 2026-07-29 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'c3d4e5f6a7b8'
down_revision = 'e0f1a2b3c4d5'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'deck_comment_reactions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('comment_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('emoji', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['comment_id'], ['deck_comments.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('comment_id', 'user_id', 'emoji', name='uq_deck_comment_reaction'),
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_unicode_ci',
    )
    op.create_index('idx_deck_comment_reaction', 'deck_comment_reactions', ['comment_id'])


def downgrade():
    op.drop_index('idx_deck_comment_reaction', table_name='deck_comment_reactions')
    op.drop_table('deck_comment_reactions')
