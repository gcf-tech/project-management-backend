"""add self-assessment tables, assessment_role column and seed data

Revision ID: e3f5a6b7c8d9
Revises: d2e4f5a6b7c8
Create Date: 2026-06-29 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e3f5a6b7c8d9'
down_revision = 'd2e4f5a6b7c8'
branch_labels = None
depends_on = None


# ── Seed data derived from the legacy data.js (codigo, cargo, area, lider) ──
# Keyed by nc_user_id so user ids are resolved at run time (portable across envs).
#   role: assessment_role for the user (admin | leader | collaborator)
#   evaluador: default assigned evaluator name for period 2026-S1 (None = top of chain)
EMPLOYEES = [
    # codigo, nc_user_id,   cargo,                                                                         area,            lider_default,     role,           evaluador
    ("0009", "lquintero",   "Chairman / Global Corporate Finance",                                         "Operaciones",   "",                "admin",        None),
    ("0012", "dquintero",   "Chief Executive Officer - CEO / Director Ejecutivo",                           "Operaciones",   "Luis Quintero",   "admin",        "Luis Quintero"),
    ("0004", "emartinez",   "Chief Operating Officer - COO / Director de Operaciones",                      "Operaciones",   "Luis Quintero",   "admin",        "Luis Quintero"),
    ("0011", "fnava",       "Chief Financial Officer - CFO / Director Financiero",                          "Administración","Edgar Martinez",  "leader",       "Daniel Quintero"),
    ("0005", "jflorez",     "Chief Technology Officer - CTO / Director de Tecnología",                      "Tecnología",    "Daniel Quintero", "leader",       "Daniel Quintero"),
    ("0014", "cocampo",     "CEO QQ Global Cryptox & QQ Digital One",                                       "Operaciones",   "Luis Quintero",   "leader",       "Luis Quintero"),
    ("0019", "esantana",    "Investment Advisor Representative / Representante Asesor de Inversiones",       "Comercial",     "Joseht Flores",   "collaborator", "Edgar Martinez"),
    ("0003", "strujillo",   "Full Stack Engineer / Ingeniero Desarrollador Full Stack",                     "Tecnología",    "Juan Florez",     "collaborator", "Juan Florez"),
    ("0002", "sgutierrez",  "Infrastructure & Support Engineer / Ingeniero de Infraestructura",             "Tecnología",    "Juan Florez",     "collaborator", "Juan Florez"),
    ("0001", "mmazo",       "RRHH / Talent Management",                                                     "Operaciones",   "Edgar Martinez",  "collaborator", "Juan Florez"),
    ("0017", "avillalobos", "Administrative Assistant / Asistente Administrativa",                          "Administración","Fabiola Nava",    "collaborator", "Fabiola Nava"),
    ("0018", "cvelasquez",  "Full Stack Engineer / Ingeniero Desarrollador Full Stack",                     "Tecnología",    "Juan Florez",     "collaborator", "Juan Florez"),
    ("0007", "cpetit",      "Sales Manager / Gerente de Ventas",                                            "Comercial",     "Joseht Flores",   "collaborator", "Edgar Martinez"),
    ("0016", "denciso",     "Portfolio Manager - Financial Q Group",                                        "Fondo",         "Edgar Martinez",  "collaborator", "Daniel Quintero"),
    ("0015", "jbocanegra",  "Audiovisual Content Creator & Editor",                                         "Marketing",     "Edgar Martinez",  "collaborator", "Edgar Martinez"),
    ("0013", "kquintero",   "Exchange Operator / Operador de Intercambio",                                  "Operaciones",   "Daniel Quintero", "collaborator", "Daniel Quintero"),
    ("0008", "mhernandez",  "Marketing Coordinator / Coordinadora de Marketing",                            "Marketing",     "Edgar Martinez",  "collaborator", "Edgar Martinez"),
    ("0010", "ssosa",       "Full Stack Engineer / Ingeniero Desarrollador Full Stack",                     "Tecnología",    "Juan Florez",     "collaborator", "Carlos Ocampo"),
    ("0006", "ycalderon",   "Customer Service / Servicio al Cliente",                                       "Comercial",     "Edgar Martinez",  "collaborator", "Edgar Martinez"),
]

BASE_PERIOD = ("2026-S1", "2026 - Semestre 1")


def upgrade():
    # 1) New column on users (managed independently, like role_commercial)
    op.add_column('users', sa.Column('assessment_role', sa.String(20), nullable=True))

    # 2) Periods
    op.create_table(
        'assessment_periods',
        sa.Column('id', sa.String(20), nullable=False),
        sa.Column('nombre', sa.String(100), nullable=False),
        sa.Column('estado', sa.Enum('activo', 'inactivo', 'cerrado'), nullable=True, server_default='inactivo'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    # 3) Employees (codigo ↔ user)
    op.create_table(
        'assessment_employees',
        sa.Column('codigo', sa.String(10), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('cargo', sa.String(255), nullable=True),
        sa.Column('area', sa.String(100), nullable=True),
        sa.Column('lider_default', sa.String(255), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True, server_default='1'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('codigo'),
        sa.UniqueConstraint('user_id'),
        sa.Index('idx_assessment_employees_user', 'user_id'),
    )

    # 4) Evaluations
    op.create_table(
        'assessment_evaluations',
        sa.Column('id', sa.String(60), nullable=False),
        sa.Column('codigo', sa.String(10), nullable=False),
        sa.Column('periodo', sa.String(20), nullable=False),
        sa.Column('evaluador', sa.String(255), nullable=True),
        sa.Column('fecha', sa.String(20), nullable=True),
        sa.Column('competencias', sa.JSON(), nullable=True),
        sa.Column('kpi', sa.DECIMAL(6, 2), nullable=True, server_default='0'),
        sa.Column('politicas', sa.DECIMAL(6, 2), nullable=True, server_default='0'),
        sa.Column('kpis_detalle', sa.JSON(), nullable=True),
        sa.Column('fortalezas', sa.Text(), nullable=True),
        sa.Column('oportunidades', sa.Text(), nullable=True),
        sa.Column('comentarios', sa.Text(), nullable=True),
        sa.Column('plan', sa.JSON(), nullable=True),
        sa.Column('estado_eval', sa.Enum('Borrador', 'Enviada', 'Cerrada'), nullable=True, server_default='Borrador'),
        sa.Column('enviada_por', sa.String(255), nullable=True),
        sa.Column('enviada_en', sa.DateTime(timezone=True), nullable=True),
        sa.Column('realizada', sa.Boolean(), nullable=True, server_default='0'),
        sa.Column('version', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['codigo'], ['assessment_employees.codigo'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['periodo'], ['assessment_periods.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('unique_codigo_periodo', 'codigo', 'periodo', unique=True),
        sa.Index('idx_assessment_eval_periodo', 'periodo'),
    )

    # 5) Version snapshots
    op.create_table(
        'assessment_versions',
        sa.Column('vid', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('eval_id', sa.String(60), nullable=False),
        sa.Column('codigo', sa.String(10), nullable=False),
        sa.Column('periodo', sa.String(20), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('snapshot', sa.JSON(), nullable=False),
        sa.Column('snapshot_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('vid'),
        sa.Index('idx_assessment_versions_codigo', 'codigo'),
        sa.Index('idx_assessment_versions_eval', 'eval_id'),
    )

    # 6) Evaluator assignments
    op.create_table(
        'assessment_evaluators',
        sa.Column('id', sa.String(60), nullable=False),
        sa.Column('codigo', sa.String(10), nullable=False),
        sa.Column('periodo', sa.String(20), nullable=False),
        sa.Column('evaluador', sa.String(255), nullable=False),
        sa.Column('evaluador_anterior', sa.String(255), nullable=True),
        sa.Column('usuario_cambio', sa.String(255), nullable=True),
        sa.Column('actualizado', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('unique_evaluator_codigo_periodo', 'codigo', 'periodo', unique=True),
        sa.Index('idx_assessment_evaluators_periodo', 'periodo'),
    )

    # 7) Audit log
    op.create_table(
        'assessment_audit',
        sa.Column('aid', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('usuario', sa.String(255), nullable=True),
        sa.Column('accion', sa.String(255), nullable=True),
        sa.Column('periodo', sa.String(20), nullable=True),
        sa.Column('valor_anterior', sa.Text(), nullable=True),
        sa.Column('valor_nuevo', sa.Text(), nullable=True),
        sa.Column('detalle', sa.Text(), nullable=True),
        sa.Column('fecha', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('aid'),
        sa.Index('idx_assessment_audit_periodo', 'periodo'),
    )

    # ── Seed ──────────────────────────────────────────────────────────────────
    conn = op.get_bind()
    period_id, period_name = BASE_PERIOD

    # Base period (active)
    conn.execute(
        sa.text(
            "INSERT INTO assessment_periods (id, nombre, estado, created_at, updated_at) "
            "VALUES (:id, :nombre, 'activo', NOW(), NOW())"
        ),
        {"id": period_id, "nombre": period_name},
    )

    for codigo, nc_user_id, cargo, area, lider, role, evaluador in EMPLOYEES:
        row = conn.execute(
            sa.text("SELECT id FROM users WHERE nc_user_id = :nc"),
            {"nc": nc_user_id},
        ).fetchone()
        if not row:
            # User not present in this environment — skip gracefully.
            continue
        user_id = row[0]

        # assessment_role on users
        conn.execute(
            sa.text("UPDATE users SET assessment_role = :role WHERE id = :uid"),
            {"role": role, "uid": user_id},
        )

        # employee row
        conn.execute(
            sa.text(
                "INSERT INTO assessment_employees "
                "(codigo, user_id, cargo, area, lider_default, is_active, created_at, updated_at) "
                "VALUES (:codigo, :uid, :cargo, :area, :lider, 1, NOW(), NOW())"
            ),
            {"codigo": codigo, "uid": user_id, "cargo": cargo, "area": area, "lider": lider},
        )

        # default evaluator assignment for the base period
        if evaluador:
            assign_id = f"AS_{codigo}_{period_id.replace('-', '')}"
            conn.execute(
                sa.text(
                    "INSERT INTO assessment_evaluators "
                    "(id, codigo, periodo, evaluador, usuario_cambio, actualizado) "
                    "VALUES (:id, :codigo, :periodo, :evaluador, 'migration', NOW())"
                ),
                {"id": assign_id, "codigo": codigo, "periodo": period_id, "evaluador": evaluador},
            )


def downgrade():
    op.drop_table('assessment_audit')
    op.drop_table('assessment_evaluators')
    op.drop_table('assessment_versions')
    op.drop_table('assessment_evaluations')
    op.drop_table('assessment_employees')
    op.drop_table('assessment_periods')
    op.drop_column('users', 'assessment_role')
