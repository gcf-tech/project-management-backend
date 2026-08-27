"""add workspace assistant threads y messages (historial de conversación)

Cuelga de '6d512cfae938' (rentabilidad de portafolios), que es la punta de esa rama.
No de 'f7a8b9c0d1e2': de ahí ya colgaban las subscripciones push, y encadenar aquí
habría abierto una cabeza hermana en vez de continuar la cadena.

El upgrade es IDEMPOTENTE: si las tablas ya existen —porque se crearon a mano con
scripts/migrate_hilos_asistente.py, que es como se migra de verdad en producción—
solo avanza la versión sin tocar nada.

Revision ID: a3c1d5e7f9b2
Revises: 6d512cfae938
Create Date: 2026-08-26 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3c1d5e7f9b2'
down_revision: Union[str, Sequence[str], None] = '6d512cfae938'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLA_HILOS = 'workspace_assistant_threads'
TABLA_MENSAJES = 'workspace_assistant_messages'


def _tablas(bind):
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    existentes = _tablas(bind)

    if TABLA_HILOS not in existentes:
        op.create_table(
            TABLA_HILOS,
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('usuario_id', sa.Integer(), nullable=False),
            sa.Column('titulo', sa.String(120), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(['usuario_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        # La lista de hilos se pide siempre del más reciente hacia atrás.
        op.create_index('idx_ws_asst_hilo_usuario', TABLA_HILOS, ['usuario_id', 'updated_at'])

    if TABLA_MENSAJES not in existentes:
        op.create_table(
            TABLA_MENSAJES,
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('hilo_id', sa.Integer(), nullable=False),
            sa.Column('rol', sa.Enum('usuario', 'asistente'), nullable=False),
            sa.Column('contenido', sa.Text(), nullable=False),
            sa.Column('origen', sa.Enum('voz', 'texto'), nullable=False, server_default='texto'),
            # Siempre UTC, y siempre estampado por el servidor.
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(['hilo_id'], [f'{TABLA_HILOS}.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('idx_ws_asst_msg_hilo', TABLA_MENSAJES, ['hilo_id', 'id'])


def downgrade() -> None:
    bind = op.get_bind()
    existentes = _tablas(bind)
    # Los mensajes primero: la FK apunta a los hilos.
    if TABLA_MENSAJES in existentes:
        op.drop_table(TABLA_MENSAJES)
    if TABLA_HILOS in existentes:
        op.drop_table(TABLA_HILOS)
