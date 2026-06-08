"""add commercial dashboard tables

Revision ID: c1b9d4e2a3f5
Revises: a1f3c7e29b14
Create Date: 2026-06-08 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = 'c1b9d4e2a3f5'
down_revision: Union[str, Sequence[str], None] = 'a1f3c7e29b14'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create commercial dashboard tables."""
    
    # Create commercial_config table
    op.create_table(
        'commercial_config',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('month', sa.Integer(), nullable=False),
        sa.Column('meta_mensual', sa.DECIMAL(12, 2), nullable=True, server_default='200000'),
        sa.Column('meta_contactos_dia', sa.Integer(), nullable=True, server_default='25'),
        sa.Column('meta_reuniones_dia', sa.Integer(), nullable=True, server_default='3'),
        sa.Column('meta_contratos_dia', sa.Integer(), nullable=True, server_default='2'),
        sa.Column('ticket_promedio', sa.DECIMAL(12, 2), nullable=True, server_default='50000'),
        sa.Column('meta_clientes_nuevos_mes', sa.Integer(), nullable=True, server_default='4'),
        sa.Column('monto_min_inversion', sa.DECIMAL(12, 2), nullable=True, server_default='50000'),
        sa.Column('pct_comision', sa.DECIMAL(5, 2), nullable=True, server_default='2.0'),
        sa.Column('umbral_verde', sa.DECIMAL(3, 2), nullable=True, server_default='1.0'),
        sa.Column('umbral_amarillo', sa.DECIMAL(3, 2), nullable=True, server_default='0.8'),
        sa.Column('negocio', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True, server_default='1'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('unique_period', 'year', 'month', unique=True)
    )
    
    # Create commercial_settings table
    op.create_table(
        'commercial_settings',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('meta_clientes', sa.Integer(), nullable=True, server_default='4'),
        sa.Column('min_inv', sa.DECIMAL(12, 2), nullable=True, server_default='50000'),
        sa.Column('comision', sa.DECIMAL(5, 2), nullable=True, server_default='2.0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('idx_commercial_settings_user', 'user_id'),
        sa.UniqueConstraint('user_id')
    )
    
    # Create commercial_daily_data table
    op.create_table(
        'commercial_daily_data',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('month', sa.Integer(), nullable=False),
        sa.Column('day', sa.Integer(), nullable=False),
        sa.Column('contactos', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('reuniones', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('contratos', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('ventas', sa.DECIMAL(12, 2), nullable=True, server_default='0'),
        sa.Column('clientes_nuevos', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('leads_nuevos', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('leads_contactados', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('leads_interesados', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('leads_info_enviada', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('leads_seguimiento', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('leads_presentacion', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('leads_negociacion', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('leads_cerrados', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('notas', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('unique_user_date', 'user_id', 'date', unique=True),
        sa.Index('idx_commercial_daily_date', 'date'),
        sa.Index('idx_commercial_daily_year_month', 'year', 'month'),
        sa.Index('idx_commercial_daily_user_year_month', 'user_id', 'year', 'month')
    )


def downgrade() -> None:
    """Drop commercial dashboard tables."""
    op.drop_table('commercial_daily_data')
    op.drop_table('commercial_settings')
    op.drop_table('commercial_config')
