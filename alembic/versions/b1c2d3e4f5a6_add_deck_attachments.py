"""add deck_attachments table (comment/card file attachments)

Revision ID: b1c2d3e4f5a6
Revises: a7b8c9d0e1f2
Create Date: 2026-07-02 15:10:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import LONGBLOB


revision = 'b1c2d3e4f5a6'
down_revision = 'a7b8c9d0e1f2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'deck_attachments',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('card_id', sa.Integer(), nullable=False),
        sa.Column('comment_id', sa.Integer(), nullable=True),
        sa.Column('uploaded_by', sa.Integer(), nullable=True),
        sa.Column('filename', sa.String(255), nullable=False),
        sa.Column('content_type', sa.String(120), nullable=True),
        sa.Column('size', sa.Integer(), nullable=True),
        sa.Column('data', LONGBLOB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['card_id'], ['deck_cards.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['comment_id'], ['deck_comments.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['uploaded_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('idx_deck_attach_card', 'card_id'),
        sa.Index('idx_deck_attach_comment', 'comment_id'),
    )


def downgrade():
    op.drop_table('deck_attachments')
