"""add workspace assistant tables (notas, recordatorios y auditoría del asistente por voz)

Revision ID: a1b2c3d4e5f6
Revises: e1f2a3b4c5d6
Create Date: 2026-08-12 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'workspace_assistant_notes',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('usuario_id', sa.Integer(), nullable=False),
        sa.Column('titulo', sa.String(255), nullable=False),
        sa.Column('cuerpo', sa.Text(), nullable=False),
        sa.Column('origen', sa.Enum('voz', 'texto'), nullable=False, server_default='voz'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['usuario_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_ws_asst_notes_usuario', 'workspace_assistant_notes', ['usuario_id', 'created_at'])

    op.create_table(
        'workspace_assistant_reminders',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('usuario_id', sa.Integer(), nullable=False),
        sa.Column('texto', sa.String(500), nullable=False),
        sa.Column('vence_en', sa.DateTime(timezone=True), nullable=False),   # siempre UTC
        sa.Column('estado', sa.Enum('pendiente', 'notificado', 'cancelado'),
                  nullable=False, server_default='pendiente'),
        sa.Column('notificado_en', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['usuario_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    # El scheduler consulta por (estado, vence_en) cada 60 s.
    op.create_index('idx_ws_asst_rem_estado_vence', 'workspace_assistant_reminders', ['estado', 'vence_en'])
    op.create_index('idx_ws_asst_rem_usuario', 'workspace_assistant_reminders', ['usuario_id', 'estado'])

    op.create_table(
        'workspace_assistant_log',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('usuario_id', sa.Integer(), nullable=False),
        sa.Column('accion', sa.String(60), nullable=False),
        sa.Column('argumentos', sa.JSON(), nullable=True),
        sa.Column('resultado', sa.Enum('ok', 'error'), nullable=False),
        sa.Column('detalle', sa.Text(), nullable=True),
        sa.Column('transcripcion', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['usuario_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_ws_asst_log_usuario', 'workspace_assistant_log', ['usuario_id', 'created_at'])

    # Los recordatorios se entregan por deck_notifications (la campana que ya
    # existe): solo hace falta un tipo propio, el cliente tolera la forma.
    op.execute(
        "ALTER TABLE deck_notifications "
        "MODIFY COLUMN type "
        "ENUM('assigned','mentioned','comment','card_updated','due_soon','moved','shared','assistant_reminder') "
        "NOT NULL"
    )


def downgrade() -> None:
    # Sin equivalente en Deck al que reasignarlas: se borran antes de encoger el enum.
    op.execute("DELETE FROM deck_notifications WHERE type = 'assistant_reminder'")
    op.execute(
        "ALTER TABLE deck_notifications "
        "MODIFY COLUMN type "
        "ENUM('assigned','mentioned','comment','card_updated','due_soon','moved','shared') "
        "NOT NULL"
    )

    op.drop_index('idx_ws_asst_log_usuario', table_name='workspace_assistant_log')
    op.drop_table('workspace_assistant_log')
    op.drop_index('idx_ws_asst_rem_usuario', table_name='workspace_assistant_reminders')
    op.drop_index('idx_ws_asst_rem_estado_vence', table_name='workspace_assistant_reminders')
    op.drop_table('workspace_assistant_reminders')
    op.drop_index('idx_ws_asst_notes_usuario', table_name='workspace_assistant_notes')
    op.drop_table('workspace_assistant_notes')
