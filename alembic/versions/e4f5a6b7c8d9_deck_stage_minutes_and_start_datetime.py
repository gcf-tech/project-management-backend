"""deck: default_minutes per stage + start_date with time

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-07-02 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'e4f5a6b7c8d9'
down_revision = 'd3e4f5a6b7c8'
branch_labels = None
depends_on = None

STAGE_DEFAULT_MINUTES = {
    'Creación': 0, 'Prototipado': 180, 'Revisión': 120, 'Desarrollo': 480,
    'Testing interno': 120, 'Testing externo': 120, 'Documentación': 60, 'Lanzado': 0,
}


def upgrade():
    op.add_column('deck_columns', sa.Column('default_minutes', sa.Integer(), nullable=True, server_default='0'))
    # start_date pasa de DATE a DATETIME para manejar horas.
    op.alter_column('deck_cards', 'start_date',
                    existing_type=sa.Date(), type_=sa.DateTime(timezone=True), existing_nullable=True)

    conn = op.get_bind()
    for title, mins in STAGE_DEFAULT_MINUTES.items():
        conn.execute(sa.text("UPDATE deck_columns SET default_minutes=:m WHERE title=:t"), {"m": mins, "t": title})


def downgrade():
    op.alter_column('deck_cards', 'start_date',
                    existing_type=sa.DateTime(timezone=True), type_=sa.Date(), existing_nullable=True)
    op.drop_column('deck_columns', 'default_minutes')
