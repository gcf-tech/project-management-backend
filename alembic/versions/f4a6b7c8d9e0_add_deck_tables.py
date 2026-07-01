"""add deck (teamwork kanban) tables, deck_role column and seed boards

Revision ID: f4a6b7c8d9e0
Revises: e3f5a6b7c8d9
Create Date: 2026-06-29 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f4a6b7c8d9e0'
down_revision = 'e3f5a6b7c8d9'
branch_labels = None
depends_on = None


# Default columns seeded on every team board.
DEFAULT_COLUMNS = ["Not started", "In progress"]


def upgrade():
    # 1) Deck access level on users (managed independently, like assessment_role)
    op.add_column('users', sa.Column('deck_role', sa.String(20), nullable=True))

    # 2) Projects (optional grouping for cards)
    op.create_table(
        'deck_projects',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('team_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(150), nullable=False),
        sa.Column('color', sa.String(20), nullable=True),
        sa.Column('archived', sa.Boolean(), nullable=True, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('idx_deck_projects_team', 'team_id'),
    )

    # 3) Boards (one per team)
    op.create_table(
        'deck_boards',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('team_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(150), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('color', sa.String(20), nullable=True),
        sa.Column('archived', sa.Boolean(), nullable=True, server_default='0'),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('uq_deck_boards_team', 'team_id', unique=True),
    )

    # 4) Columns (task lists)
    op.create_table(
        'deck_columns',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('board_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(120), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('color', sa.String(20), nullable=True),
        sa.Column('is_default', sa.Boolean(), nullable=True, server_default='0'),
        sa.Column('wip_limit', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['board_id'], ['deck_boards.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('idx_deck_columns_board_pos', 'board_id', 'position'),
    )

    # 5) Tags (board-scoped labels)
    op.create_table(
        'deck_tags',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('board_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(60), nullable=False),
        sa.Column('color', sa.String(20), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['board_id'], ['deck_boards.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('uq_deck_tags_board_name', 'board_id', 'name', unique=True),
    )

    # 6) Cards
    op.create_table(
        'deck_cards',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('board_id', sa.Integer(), nullable=False),
        sa.Column('column_id', sa.Integer(), nullable=True),
        sa.Column('owner_team_id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=True),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('priority', sa.Enum('low', 'medium', 'high', 'urgent'), nullable=True),
        sa.Column('start_date', sa.Date(), nullable=True),
        sa.Column('due_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('archived', sa.Boolean(), nullable=True, server_default='0'),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('client_op_id', sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(['board_id'], ['deck_boards.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['column_id'], ['deck_columns.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['owner_team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['project_id'], ['deck_projects.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('client_op_id'),
        sa.Index('idx_deck_cards_column_pos', 'column_id', 'position'),
        sa.Index('idx_deck_cards_board', 'board_id'),
        sa.Index('idx_deck_cards_owner_team', 'owner_team_id'),
        sa.Index('idx_deck_cards_due', 'due_date'),
    )

    # 7) Card ↔ assignee (M2M)
    op.create_table(
        'deck_card_assignees',
        sa.Column('card_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('assigned_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['card_id'], ['deck_cards.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['assigned_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('card_id', 'user_id'),
        sa.Index('idx_deck_assignee_user', 'user_id'),
    )

    # 8) Card ↔ follower (M2M)
    op.create_table(
        'deck_card_followers',
        sa.Column('card_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['card_id'], ['deck_cards.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('card_id', 'user_id'),
        sa.Index('idx_deck_follower_user', 'user_id'),
    )

    # 9) Card ↔ team (M2M, cross-team sharing)
    op.create_table(
        'deck_card_teams',
        sa.Column('card_id', sa.Integer(), nullable=False),
        sa.Column('team_id', sa.Integer(), nullable=False),
        sa.Column('is_owner', sa.Boolean(), nullable=True, server_default='0'),
        sa.Column('shared_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['card_id'], ['deck_cards.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['shared_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('card_id', 'team_id'),
        sa.Index('idx_deck_card_teams_team', 'team_id'),
    )

    # 10) Card ↔ tag (M2M)
    op.create_table(
        'deck_card_tags',
        sa.Column('card_id', sa.Integer(), nullable=False),
        sa.Column('tag_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['card_id'], ['deck_cards.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tag_id'], ['deck_tags.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('card_id', 'tag_id'),
        sa.Index('idx_deck_card_tags_tag', 'tag_id'),
    )

    # 11) Comments
    op.create_table(
        'deck_comments',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('card_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('parent_id', sa.Integer(), nullable=True),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('mentions', sa.JSON(), nullable=True),
        sa.Column('edited_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['card_id'], ['deck_cards.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['parent_id'], ['deck_comments.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('idx_deck_comments_card', 'card_id', 'created_at'),
    )

    # 12) Activity (immutable event log)
    op.create_table(
        'deck_activity',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('card_id', sa.Integer(), nullable=False),
        sa.Column('board_id', sa.Integer(), nullable=False),
        sa.Column('actor_id', sa.Integer(), nullable=True),
        sa.Column('event_type', sa.Enum(
            'created', 'updated', 'moved', 'assigned', 'unassigned',
            'tagged', 'untagged', 'due_changed', 'start_changed',
            'completed', 'reopened', 'commented', 'followed', 'unfollowed',
            'shared_team', 'unshared_team', 'archived', 'restored',
        ), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=True),
        sa.Column('message', sa.String(500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['card_id'], ['deck_cards.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['board_id'], ['deck_boards.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('idx_deck_activity_card', 'card_id', 'created_at'),
        sa.Index('idx_deck_activity_board', 'board_id', 'created_at'),
    )

    # 13) Notifications
    op.create_table(
        'deck_notifications',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('actor_id', sa.Integer(), nullable=True),
        sa.Column('card_id', sa.Integer(), nullable=True),
        sa.Column('activity_id', sa.Integer(), nullable=True),
        sa.Column('type', sa.Enum(
            'assigned', 'mentioned', 'comment', 'card_updated',
            'due_soon', 'moved', 'shared',
        ), nullable=False),
        sa.Column('message', sa.String(500), nullable=True),
        sa.Column('is_read', sa.Boolean(), nullable=True, server_default='0'),
        sa.Column('nc_pushed', sa.Boolean(), nullable=True, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['card_id'], ['deck_cards.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['activity_id'], ['deck_activity.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('idx_deck_notif_user_unread', 'user_id', 'is_read', 'created_at'),
        sa.Index('idx_deck_notif_card', 'card_id'),
    )

    # ── Seed: one board + default columns per existing team ──────────────────
    conn = op.get_bind()
    teams = conn.execute(sa.text("SELECT id, name FROM teams")).fetchall()
    for team_id, team_name in teams:
        res = conn.execute(
            sa.text(
                "INSERT INTO deck_boards (team_id, title, archived, created_at, updated_at) "
                "VALUES (:tid, :title, 0, NOW(), NOW())"
            ),
            {"tid": team_id, "title": f"{team_name} Board"},
        )
        board_id = res.lastrowid
        for pos, name in enumerate(DEFAULT_COLUMNS):
            conn.execute(
                sa.text(
                    "INSERT INTO deck_columns (board_id, title, position, is_default, created_at, updated_at) "
                    "VALUES (:bid, :title, :pos, 1, NOW(), NOW())"
                ),
                {"bid": board_id, "title": name, "pos": pos},
            )


def downgrade():
    op.drop_table('deck_notifications')
    op.drop_table('deck_activity')
    op.drop_table('deck_comments')
    op.drop_table('deck_card_tags')
    op.drop_table('deck_card_teams')
    op.drop_table('deck_card_followers')
    op.drop_table('deck_card_assignees')
    op.drop_table('deck_cards')
    op.drop_table('deck_tags')
    op.drop_table('deck_columns')
    op.drop_table('deck_boards')
    op.drop_table('deck_projects')
    op.drop_column('users', 'deck_role')
