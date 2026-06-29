from sqlalchemy import (
    Column, Integer, String, Text, Boolean,
    Enum, Date, DateTime, DECIMAL, ForeignKey, CheckConstraint, Time, Index, JSON
)
from sqlalchemy.orm import relationship
from app.db.database import Base
from app.core.datetime_utils import utc_now


class Team(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    leader_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    parent_team_id = Column(Integer, ForeignKey("teams.id", ondelete="SET NULL"), nullable=True)
    is_tech_team = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    members = relationship("User", back_populates="team", foreign_keys="User.team_id")
    leader = relationship("User", foreign_keys=[leader_id], post_update=True)
    parent_team = relationship("Team", remote_side=[id], backref="sub_teams")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nc_user_id = Column(String(100), nullable=False, unique=True)
    display_name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=True)
    job_title = Column(String(100), nullable=True)
    team_id = Column(Integer, ForeignKey("teams.id", ondelete="SET NULL"), nullable=True)
    role = Column(Enum("member", "leader", "admin"), default="member")
    role_commercial = Column(String(50), nullable=True)  # Derived from team_id: 7→admin, 2→commercial
    # Self-Assessment access level, managed independently (like role_commercial):
    #   "admin"        → full access to every evaluation + admin modules
    #   "leader"       → evaluates their assigned team + their own self-evaluation
    #   "collaborator" → self-evaluation only
    #   "viewer"       → read-only across the holding
    #   NULL           → no access to the assessment dashboard
    assessment_role = Column(String(20), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    team = relationship("Team", back_populates="members", foreign_keys=[team_id])
    tasks = relationship("Task", back_populates="owner", foreign_keys="Task.owner_id")
    activities = relationship("Activity", back_populates="owner", foreign_keys="Activity.owner_id")
    skills = relationship("UserSkill", back_populates="user")


class Task(Base):
    __tablename__ = "tasks"

    id = Column(String(50), primary_key=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    assigned_to = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    column_status = Column(Enum("actively-working", "working-now", "completed"), default="actively-working")
    type = Column(Enum("project", "task"), default="project")
    priority = Column(Enum("low", "medium", "high", "urgent"), nullable=True)
    start_date = Column(Date, nullable=True)
    deadline = Column(Date, nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    progress = Column(Integer, default=0)
    time_spent = Column(Integer, default=0)
    difficulty = Column(Integer, nullable=True)
    difficulty_reason = Column(Text, nullable=True)
    was_difficult = Column(Boolean, default=False)
    deck_card_id = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # Idempotency key sent by the client on POST /tareas so a double-click /
    # network retry returns the same row instead of inserting duplicates.
    client_op_id = Column(String(64), unique=True, nullable=True, index=True)

    owner = relationship("User", back_populates="tasks", foreign_keys=[owner_id])
    assignee = relationship("User", foreign_keys=[assigned_to])
    subtasks = relationship("Subtask", back_populates="task", cascade="all, delete-orphan")
    time_logs = relationship("TimeLog", back_populates="task", cascade="all, delete-orphan")
    observations = relationship("Observation", back_populates="task", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("difficulty BETWEEN 1 AND 10", name="chk_difficulty"),
    )


class Activity(Base):
    __tablename__ = "activities"

    id = Column(String(50), primary_key=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    assigned_to = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    type = Column(String(50), nullable=False, default="other")
    priority = Column(Enum("low", "medium", "high", "urgent"), nullable=True)
    start_date = Column(Date, nullable=True)
    deadline = Column(Date, nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    progress = Column(Integer, default=0)
    time_spent = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # See Task.client_op_id — same idempotency contract for POST /activities.
    client_op_id = Column(String(64), unique=True, nullable=True, index=True)

    owner = relationship("User", back_populates="activities", foreign_keys=[owner_id])
    assignee = relationship("User", foreign_keys=[assigned_to])
    time_logs = relationship("TimeLog", back_populates="activity", cascade="all, delete-orphan")
    observations = relationship("Observation", back_populates="activity", cascade="all, delete-orphan")


class Subtask(Base):
    __tablename__ = "subtasks"

    id = Column(String(50), primary_key=True)
    task_id = Column(String(50), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    text = Column(String(500), nullable=False)
    completed = Column(Boolean, default=False)
    time_spent = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    task = relationship("Task", back_populates="subtasks")


class TimeLog(Base):
    __tablename__ = "time_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    task_id = Column(String(50), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True)
    activity_id = Column(String(50), ForeignKey("activities.id", ondelete="CASCADE"), nullable=True)
    log_date = Column(Date, nullable=False)
    seconds = Column(Integer, default=0)
    start_at = Column(DateTime(timezone=True), nullable=True)
    end_at = Column(DateTime(timezone=True), nullable=True)
    client_op_id = Column(String(64), unique=True, nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    user = relationship("User")
    task = relationship("Task", back_populates="time_logs")
    activity = relationship("Activity", back_populates="time_logs")

    __table_args__ = (
        Index("idx_time_logs_user_logdate", "user_id", "log_date"),
        Index("idx_time_logs_user_task_logdate", "user_id", "task_id", "log_date"),
        Index("idx_time_logs_user_activity_logdate", "user_id", "activity_id", "log_date"),
    )


class Observation(Base):
    __tablename__ = "observations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(50), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True)
    activity_id = Column(String(50), ForeignKey("activities.id", ondelete="CASCADE"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    task = relationship("Task", back_populates="observations")
    activity = relationship("Activity", back_populates="observations")
    user = relationship("User")


class Skill(Base):
    __tablename__ = "skills"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    category = Column(Enum("frontend", "backend", "devops", "data", "design", "soft_skill", "other"), default="other")
    description = Column(String(255), nullable=True)
    is_tech_only = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)


class UserSkill(Base):
    __tablename__ = "user_skills"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    skill_id = Column(Integer, ForeignKey("skills.id", ondelete="CASCADE"), nullable=False)
    self_score = Column(Integer, default=5)
    avg_endorsement_score = Column(DECIMAL(3, 1), default=0)
    total_endorsements = Column(Integer, default=0)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    user = relationship("User", back_populates="skills")
    skill = relationship("Skill")
    endorsements = relationship("SkillEndorsement", back_populates="user_skill", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("self_score BETWEEN 1 AND 10", name="chk_self_score"),
    )


class SkillEndorsement(Base):
    __tablename__ = "skill_endorsements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_skill_id = Column(Integer, ForeignKey("user_skills.id", ondelete="CASCADE"), nullable=False)
    endorsed_by = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    score = Column(Integer, nullable=False)
    comment = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    user_skill = relationship("UserSkill", back_populates="endorsements")
    endorser = relationship("User")

    __table_args__ = (
        CheckConstraint("score BETWEEN 1 AND 10", name="chk_endorsement_score"),
    )


class UserPreferences(Base):
    __tablename__ = "user_preferences"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    week_start_day = Column(Integer, default=1, nullable=False)
    week_end_day = Column(Integer, default=5, nullable=False)
    calendar_view = Column(String(20), nullable=False, default="week")
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    user = relationship("User")


class WeeklyBlock(Base):
    __tablename__ = "weekly_blocks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    week_start = Column(Date, nullable=False)
    day_of_week = Column(Integer, nullable=False)
    block_type = Column(Enum("task", "activity", "personal"), nullable=False)
    task_id = Column(String(50), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    activity_id = Column(String(50), ForeignKey("activities.id", ondelete="SET NULL"), nullable=True)
    title = Column(String(200), nullable=True)
    color = Column(String(20), nullable=True)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    notes = Column(Text, nullable=True)
    recurrence = Column(Enum("none", "weekly"), nullable=False, default="none")
    recurrence_until = Column(Date, nullable=True)
    series_id = Column(String(36), nullable=True)
    # ── RRule columns (Phase 3) ──────────────────────────────────────────────
    rrule_string    = Column(String(500), nullable=True)
    dtstart         = Column(DateTime(timezone=True), nullable=True)
    rrule_until     = Column(DateTime(timezone=True), nullable=True)
    parent_block_id = Column(Integer, ForeignKey("weekly_blocks.id", ondelete="SET NULL"), nullable=True)
    exception_dates = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    user = relationship("User")
    task = relationship("Task")
    activity = relationship("Activity")
    parent_block = relationship("WeeklyBlock", remote_side="WeeklyBlock.id", foreign_keys=[parent_block_id])

    __table_args__ = (
        CheckConstraint("end_time > start_time", name="chk_weekly_block_end_after_start"),
        CheckConstraint(
            "(block_type != 'task' OR task_id IS NOT NULL)",
            name="chk_weekly_block_task_id",
        ),
        CheckConstraint(
            "(block_type != 'activity' OR activity_id IS NOT NULL)",
            name="chk_weekly_block_activity_id",
        ),
        CheckConstraint(
            "(block_type != 'personal' OR title IS NOT NULL)",
            name="chk_weekly_block_personal_title",
        ),
        Index("idx_weekly_blocks_user_week", "user_id", "week_start"),
        Index("idx_weekly_blocks_series", "series_id"),
        # Composite index for the get_virtual_projections / series-scoped queries
        # (e.g. delete_materializations_*). MySQL EXPLAIN should now report
        # type=ref instead of falling back to a non-prefix scan.
        Index("idx_weekly_blocks_user_series", "user_id", "series_id"),
    )


# ============================================================
# COMMERCIAL DASHBOARD MODELS
# ============================================================

class CommercialConfig(Base):
    """Configuración global del dashboard comercial por periodo"""
    __tablename__ = "commercial_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)  # 0-11 (enero=0, diciembre=11)
    meta_mensual = Column(DECIMAL(12, 2), default=200000)
    meta_contactos_dia = Column(Integer, default=25)
    meta_reuniones_dia = Column(Integer, default=3)
    meta_contratos_dia = Column(Integer, default=2)
    ticket_promedio = Column(DECIMAL(12, 2), default=50000)
    meta_clientes_nuevos_mes = Column(Integer, default=4)
    monto_min_inversion = Column(DECIMAL(12, 2), default=50000)
    pct_comision = Column(DECIMAL(5, 2), default=2.0)
    umbral_verde = Column(DECIMAL(3, 2), default=1.0)
    umbral_amarillo = Column(DECIMAL(3, 2), default=0.8)
    negocio = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    __table_args__ = (
        Index("unique_period", "year", "month", unique=True),
    )


class CommercialSettings(Base):
    """Configuración individual por comercial (extiende User)"""
    __tablename__ = "commercial_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    meta = Column(DECIMAL(12, 2), default=0)  # meta de capital individual por mes
    meta_clientes = Column(Integer, default=4)  # meta individual de clientes nuevos por mes
    min_inv = Column(DECIMAL(12, 2), default=50000)  # monto mínimo inversión individual
    comision = Column(DECIMAL(5, 2), default=2.0)  # % comisión individual
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    user = relationship("User")

    __table_args__ = (
        Index("idx_commercial_settings_user", "user_id"),
    )


class CommercialDailyData(Base):
    """Datos diarios por comercial"""
    __tablename__ = "commercial_daily_data"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    date = Column(Date, nullable=False)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)  # 0-11
    day = Column(Integer, nullable=False)  # 1-31

    # Actividad del día
    contactos = Column(Integer, default=0)
    reuniones = Column(Integer, default=0)
    contratos = Column(Integer, default=0)
    ventas = Column(DECIMAL(12, 2), default=0)
    clientes_nuevos = Column(Integer, default=0)

    # Tracking del funnel
    leads_nuevos = Column(Integer, default=0)
    leads_contactados = Column(Integer, default=0)
    leads_interesados = Column(Integer, default=0)
    leads_info_enviada = Column(Integer, default=0)
    leads_seguimiento = Column(Integer, default=0)
    leads_presentacion = Column(Integer, default=0)
    leads_negociacion = Column(Integer, default=0)
    leads_cerrados = Column(Integer, default=0)

    # Notas del día
    notas = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    user = relationship("User")

    __table_args__ = (
        Index("unique_user_date", "user_id", "date", unique=True),
        Index("idx_commercial_daily_date", "date"),
        Index("idx_commercial_daily_year_month", "year", "month"),
        Index("idx_commercial_daily_user_year_month", "user_id", "year", "month"),
    )


# ============================================================
# SELF-ASSESSMENT (Evaluación de Desempeño) MODELS
# ============================================================

class AssessmentPeriod(Base):
    """Período (semestre) de evaluación. Espejo de DB.listarPeriodos del front."""
    __tablename__ = "assessment_periods"

    id = Column(String(20), primary_key=True)          # p.ej. "2026-S1"
    nombre = Column(String(100), nullable=False)        # "2026 - Semestre 1"
    estado = Column(Enum("activo", "inactivo", "cerrado"), default="inactivo")
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class AssessmentEmployee(Base):
    """Catálogo de colaboradores evaluables. Vincula el código del front (p.ej.
    "0019") con el usuario real (nc_user_id) y guarda los metadatos del cargo
    que no viven en la tabla users (cargo, área, líder por defecto)."""
    __tablename__ = "assessment_employees"

    codigo = Column(String(10), primary_key=True)      # "0019"
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    cargo = Column(String(255), nullable=True)
    area = Column(String(100), nullable=True)
    lider_default = Column(String(255), nullable=True)  # nombre del líder por defecto
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    user = relationship("User")

    __table_args__ = (
        Index("idx_assessment_employees_user", "user_id"),
    )


class AssessmentEvaluation(Base):
    """Evaluación vigente: 1 por colaborador (código) y período."""
    __tablename__ = "assessment_evaluations"

    id = Column(String(60), primary_key=True)          # "EV_0019_2026S1"
    codigo = Column(String(10), ForeignKey("assessment_employees.codigo", ondelete="CASCADE"), nullable=False)
    periodo = Column(String(20), ForeignKey("assessment_periods.id", ondelete="CASCADE"), nullable=False)

    evaluador = Column(String(255), nullable=True)      # nombre del evaluador asignado
    fecha = Column(String(20), nullable=True)           # fecha de evaluación (ISO yyyy-mm-dd)

    # Estructuras ricas almacenadas como JSON (espejo del objeto `ev` del front)
    competencias = Column(JSON, nullable=True)          # [{self,lead}] x N
    kpi = Column(DECIMAL(6, 2), default=0)              # KPI manual (si no hay tabla detalle)
    politicas = Column(DECIMAL(6, 2), default=0)        # 0-10
    kpis_detalle = Column(JSON, nullable=True)          # [{nombre,meta,peso,cumplimiento}]
    fortalezas = Column(Text, nullable=True)
    oportunidades = Column(Text, nullable=True)
    comentarios = Column(Text, nullable=True)
    plan = Column(JSON, nullable=True)                  # {responsable,fecha,estado,seguimiento}

    estado_eval = Column(Enum("Borrador", "Enviada", "Cerrada"), default="Borrador")
    enviada_por = Column(String(255), nullable=True)
    enviada_en = Column(DateTime(timezone=True), nullable=True)
    realizada = Column(Boolean, default=False)
    version = Column(Integer, default=0)

    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    __table_args__ = (
        Index("unique_codigo_periodo", "codigo", "periodo", unique=True),
        Index("idx_assessment_eval_periodo", "periodo"),
    )


class AssessmentVersion(Base):
    """Snapshot inmutable de cada guardado (versionado/auditoría)."""
    __tablename__ = "assessment_versions"

    vid = Column(Integer, primary_key=True, autoincrement=True)
    eval_id = Column(String(60), nullable=False)
    codigo = Column(String(10), nullable=False)
    periodo = Column(String(20), nullable=False)
    version = Column(Integer, nullable=False)
    snapshot = Column(JSON, nullable=False)             # objeto ev completo serializado
    snapshot_at = Column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        Index("idx_assessment_versions_codigo", "codigo"),
        Index("idx_assessment_versions_eval", "eval_id"),
    )


class AssessmentEvaluator(Base):
    """Asignación evaluador↔colaborador por período."""
    __tablename__ = "assessment_evaluators"

    id = Column(String(60), primary_key=True)          # "AS_0019_2026S1"
    codigo = Column(String(10), nullable=False)
    periodo = Column(String(20), nullable=False)
    evaluador = Column(String(255), nullable=False)     # nombre del evaluador
    evaluador_anterior = Column(String(255), nullable=True)
    usuario_cambio = Column(String(255), nullable=True)
    actualizado = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    __table_args__ = (
        Index("unique_evaluator_codigo_periodo", "codigo", "periodo", unique=True),
        Index("idx_assessment_evaluators_periodo", "periodo"),
    )


class AssessmentAudit(Base):
    """Bitácora de auditoría de acciones sensibles."""
    __tablename__ = "assessment_audit"

    aid = Column(Integer, primary_key=True, autoincrement=True)
    usuario = Column(String(255), nullable=True)
    accion = Column(String(255), nullable=True)
    periodo = Column(String(20), nullable=True)
    valor_anterior = Column(Text, nullable=True)
    valor_nuevo = Column(Text, nullable=True)
    detalle = Column(Text, nullable=True)
    fecha = Column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        Index("idx_assessment_audit_periodo", "periodo"),
    )