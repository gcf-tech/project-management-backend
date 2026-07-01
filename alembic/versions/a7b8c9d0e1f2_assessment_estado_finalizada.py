"""add 'Finalizada' to assessment_evaluations.estado_eval enum

Revision ID: a7b8c9d0e1f2
Revises: f4a6b7c8d9e0
Create Date: 2026-07-01 12:00:00.000000

"""
from alembic import op


revision = 'a7b8c9d0e1f2'
down_revision = 'f4a6b7c8d9e0'
branch_labels = None
depends_on = None


def upgrade():
    # Add the intermediate/terminal "Finalizada" state used by the evaluator step.
    op.execute(
        "ALTER TABLE assessment_evaluations "
        "MODIFY COLUMN estado_eval "
        "ENUM('Borrador','Enviada','Finalizada','Cerrada') "
        "NOT NULL DEFAULT 'Borrador'"
    )


def downgrade():
    # Collapse any Finalizada back to Cerrada before shrinking the enum.
    op.execute("UPDATE assessment_evaluations SET estado_eval = 'Cerrada' WHERE estado_eval = 'Finalizada'")
    op.execute(
        "ALTER TABLE assessment_evaluations "
        "MODIFY COLUMN estado_eval "
        "ENUM('Borrador','Enviada','Cerrada') "
        "NOT NULL DEFAULT 'Borrador'"
    )
