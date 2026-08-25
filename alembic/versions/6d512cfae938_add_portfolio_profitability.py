"""add portfolio profitability

Revision ID: 6d512cfae938
Revises: a9f3c1b7d2e4
Create Date: 2026-08-25 16:01:40.290656

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6d512cfae938'
down_revision: Union[str, Sequence[str], None] = 'a9f3c1b7d2e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'portafolio_rentabilidad',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('portafolio_id', sa.Integer(), nullable=False),
        sa.Column('anio', sa.Integer(), nullable=False),
        sa.Column('mes', sa.Integer(), nullable=False),                 # 1-12
        sa.Column('valor', sa.DECIMAL(6, 2), nullable=False),           # % (admite negativos)
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['portafolio_id'], ['portafolios.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        # Una celda única por portafolio/año/mes.
        sa.Index('uq_portafolio_rentabilidad_cell', 'portafolio_id', 'anio', 'mes', unique=True),
        # Para traer/borrar un año de un portafolio de golpe.
        sa.Index('idx_portafolio_rentabilidad_pa', 'portafolio_id', 'anio'),
    )


def downgrade() -> None:
    op.drop_table('portafolio_rentabilidad')
