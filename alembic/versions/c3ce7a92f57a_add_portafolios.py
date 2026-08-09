"""add portafolios

Revision ID: c3ce7a92f57a
Revises: a7b8c9d0e1f3
Create Date: 2026-08-05 12:17:05.546285

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3ce7a92f57a'
down_revision: Union[str, Sequence[str], None] = 'a7b8c9d0e1f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'portafolios',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('nombre', sa.String(100), nullable=False),
        sa.Column('monto_minimo', sa.DECIMAL(14, 2), nullable=False),
        sa.Column('rendimiento_anual', sa.DECIMAL(5, 2), nullable=False),   # en %
        sa.Column('nivel_riesgo', sa.DECIMAL(5, 2), nullable=False),        # en %
        sa.Column('orden', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('activo', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('notas', sa.Text(), nullable=True),
        # Timestamps gestionados por el ORM (default/onupdate=utc_now en el modelo),
        # igual que el resto de tus tablas. La vigencia = updated_at, que el ORM
        # refresca en cada edición.
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('nombre', name='uq_portafolios_nombre'),
        sa.Index('idx_portafolios_orden', 'activo', 'orden'),
    )


def downgrade() -> None:
    op.drop_table('portafolios')
