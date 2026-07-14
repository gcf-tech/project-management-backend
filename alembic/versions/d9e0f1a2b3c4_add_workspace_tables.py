"""add workspace tables

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-07-13 10:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd9e0f1a2b3c4'
down_revision: Union[str, Sequence[str], None] = 'c8d9e0f1a2b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create workspace (oficina virtual) tables migrated from Supabase."""

    op.create_table(
        'workspace_profiles',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('empresa', sa.String(150), nullable=True),
        sa.Column('departamento', sa.String(100), nullable=True),
        sa.Column('avatar', sa.JSON(), nullable=True),
        sa.Column('ultima_actividad', sa.String(255), nullable=True),
        sa.Column('ultima_actividad_en', sa.DateTime(timezone=True), nullable=True),
        sa.Column('proyecto', sa.String(255), nullable=True),
        sa.Column('rendimiento', sa.Integer(), nullable=True),
        sa.Column('estado', sa.String(50), nullable=True),
        sa.Column('onboarded', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id'),
    )

    op.create_table(
        'workspace_sessions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('inicio', sa.DateTime(timezone=True), nullable=False),
        sa.Column('fin', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('idx_workspace_sessions_user', 'user_id', 'inicio'),
        sa.Index('idx_workspace_sessions_user_open', 'user_id', 'fin'),
    )

    op.create_table(
        'workspace_daily_time',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('minutos', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('uq_workspace_daily_user_fecha', 'user_id', 'fecha', unique=True),
        sa.Index('idx_workspace_daily_fecha', 'fecha'),
    )

    op.create_table(
        'workspace_activities',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('actividad', sa.String(500), nullable=False),
        sa.Column('momento', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('idx_workspace_activities_user_momento', 'user_id', 'momento'),
    )

    op.create_table(
        'workspace_tasks',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('texto', sa.String(500), nullable=False),
        sa.Column('completada', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('creado_en', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('idx_workspace_tasks_user_fecha', 'user_id', 'fecha'),
    )

    op.create_table(
        'workspace_messages',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('de_id', sa.Integer(), nullable=False),
        sa.Column('para_id', sa.Integer(), nullable=False),
        sa.Column('texto', sa.Text(), nullable=False),
        sa.Column('creado_en', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['de_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['para_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('idx_workspace_msg_pair', 'de_id', 'para_id', 'creado_en'),
        sa.Index('idx_workspace_msg_para', 'para_id', 'de_id', 'creado_en'),
    )

    op.create_table(
        'workspace_workstations',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('dept_id', sa.String(100), nullable=False),
        sa.Column('x', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('y', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('usuario_id', sa.Integer(), nullable=True),
        sa.Column('etiqueta', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['usuario_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('idx_workspace_puestos_dept', 'dept_id'),
        sa.Index('idx_workspace_puestos_user', 'usuario_id'),
    )


def downgrade() -> None:
    """Drop workspace tables (reverse dependency order)."""
    op.drop_table('workspace_workstations')
    op.drop_table('workspace_messages')
    op.drop_table('workspace_tasks')
    op.drop_table('workspace_activities')
    op.drop_table('workspace_daily_time')
    op.drop_table('workspace_sessions')
    op.drop_table('workspace_profiles')
