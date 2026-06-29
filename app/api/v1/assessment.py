"""
Self-Assessment (Evaluación de Desempeño) API endpoints.

Mirrors the legacy client-side DB.* API of the Self-Assessment dashboard,
backed by MySQL and protected by Nextcloud OAuth. Access is governed by
users.assessment_role (admin | leader | collaborator | viewer):

  - admin        → every evaluation + admin modules (periods, evaluators, audit)
  - leader       → evaluates their assigned team + their own self-evaluation
  - collaborator → self-evaluation only
  - viewer       → read-only across the holding

Every employee (codigo) is at least a collaborator on their own record.
"""
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from sqlalchemy import and_
from typing import Annotated, List, Optional, Dict, Any
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, Field

from app.api.dependencies import get_db
from app.core.security import get_nc_user_info
from app.core.datetime_utils import utc_now
from app.db.models import (
    User,
    AssessmentPeriod,
    AssessmentEmployee,
    AssessmentEvaluation,
    AssessmentVersion,
    AssessmentEvaluator,
    AssessmentAudit,
)

router = APIRouter()


# ============================================================
# SCHEMAS
# ============================================================

class EvaluationIn(BaseModel):
    codigo: str
    periodo: str
    evaluador: Optional[str] = None
    fecha: Optional[str] = None
    competencias: List[Dict[str, Any]] = Field(default_factory=list)
    kpi: float = 0
    politicas: float = 0
    kpisDetalle: List[Dict[str, Any]] = Field(default_factory=list)
    fortalezas: Optional[str] = ""
    oportunidades: Optional[str] = ""
    comentarios: Optional[str] = ""
    plan: Dict[str, Any] = Field(default_factory=dict)
    estadoEval: Optional[str] = "Borrador"
    realizada: bool = False


class PeriodIn(BaseModel):
    id: str
    nombre: str
    estado: Optional[str] = "inactivo"


class EvaluatorIn(BaseModel):
    codigo: str
    periodo: str
    evaluador: str


class AuditIn(BaseModel):
    accion: Optional[str] = None
    periodo: Optional[str] = None
    valorAnterior: Optional[str] = None
    valorNuevo: Optional[str] = None
    detalle: Optional[str] = None


# ============================================================
# AUTH / CONTEXT HELPERS
# ============================================================

async def _get_current_user(authorization: str, db: Session) -> User:
    nc_data = await get_nc_user_info(authorization)
    user = db.query(User).filter(User.nc_user_id == nc_data["id"]).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _active_period_id(db: Session) -> Optional[str]:
    p = db.query(AssessmentPeriod).filter(AssessmentPeriod.estado == "activo").first()
    if p:
        return p.id
    p = db.query(AssessmentPeriod).order_by(AssessmentPeriod.id.desc()).first()
    return p.id if p else None


class Context:
    """Resolved access context for the current user."""
    def __init__(self, user, role, codigo, nombre, visible_codes):
        self.user = user
        self.role = role                      # admin | leader | collaborator | viewer
        self.codigo = codigo                  # own employee code (or None)
        self.nombre = nombre                  # own display name
        self.visible_codes = visible_codes    # set[str] or None (= all)

    def can_see(self, codigo: str) -> bool:
        return self.visible_codes is None or codigo in self.visible_codes

    def is_admin(self) -> bool:
        return self.role == "admin"


def _build_context(db: Session, user: User, periodo: str) -> Context:
    own = db.query(AssessmentEmployee).filter(AssessmentEmployee.user_id == user.id).first()
    own_codigo = own.codigo if own else None
    nombre = user.display_name

    role = user.assessment_role
    if not role:
        # Any registered employee can at least self-evaluate.
        role = "collaborator" if own_codigo else None
    if role is None:
        raise HTTPException(status_code=403, detail="No access to assessment dashboard")

    if role in ("admin", "viewer"):
        visible = None  # all
    elif role == "leader":
        # Codes this leader evaluates this period + their own record.
        rows = db.query(AssessmentEvaluator).filter(
            and_(
                AssessmentEvaluator.periodo == periodo,
                AssessmentEvaluator.evaluador == nombre,
            )
        ).all()
        visible = {r.codigo for r in rows}
        # Fallback to default-leader employees if no explicit assignments exist.
        if not visible:
            emps = db.query(AssessmentEmployee).filter(
                AssessmentEmployee.lider_default == nombre
            ).all()
            visible = {e.codigo for e in emps}
        if own_codigo:
            visible.add(own_codigo)
    else:  # collaborator
        visible = {own_codigo} if own_codigo else set()

    return Context(user, role, own_codigo, nombre, visible)


def _assigned_evaluator(db: Session, codigo: str, periodo: str) -> Optional[str]:
    row = db.query(AssessmentEvaluator).filter(
        and_(AssessmentEvaluator.codigo == codigo, AssessmentEvaluator.periodo == periodo)
    ).first()
    if row:
        return row.evaluador
    emp = db.query(AssessmentEmployee).filter(AssessmentEmployee.codigo == codigo).first()
    return emp.lider_default if emp else None


# ============================================================
# SERIALIZATION
# ============================================================

def _employee_meta(db: Session) -> Dict[str, Dict[str, Any]]:
    """codigo -> {nombre, cargo, area, lider, userId, nc_user_id}"""
    out = {}
    rows = (
        db.query(AssessmentEmployee, User)
        .join(User, AssessmentEmployee.user_id == User.id)
        .all()
    )
    for emp, user in rows:
        out[emp.codigo] = {
            "codigo": emp.codigo,
            "nombre": user.display_name,
            "cargo": emp.cargo or "",
            "area": emp.area or "",
            "lider": emp.lider_default or "",
            "userId": user.id,
            "nc_user_id": user.nc_user_id,
        }
    return out


def _serialize_eval(ev: AssessmentEvaluation, meta: Dict[str, Any], evaluador: Optional[str]) -> Dict[str, Any]:
    return {
        "id": ev.id,
        "codigo": ev.codigo,
        "nombre": meta.get("nombre", ""),
        "cargo": meta.get("cargo", ""),
        "area": meta.get("area", ""),
        "lider": meta.get("lider", ""),
        "evaluador": ev.evaluador or evaluador or "",
        "fecha": ev.fecha or "",
        "periodo": ev.periodo,
        "competencias": ev.competencias or [],
        "kpi": float(ev.kpi) if ev.kpi is not None else 0,
        "politicas": float(ev.politicas) if ev.politicas is not None else 0,
        "kpisDetalle": ev.kpis_detalle or [],
        "fortalezas": ev.fortalezas or "",
        "oportunidades": ev.oportunidades or "",
        "comentarios": ev.comentarios or "",
        "plan": ev.plan or {"responsable": "", "fecha": "", "estado": "Pendiente", "seguimiento": ""},
        "estadoEval": ev.estado_eval or "Borrador",
        "enviadaPor": ev.enviada_por or "",
        "enviadaEn": ev.enviada_en.isoformat() if ev.enviada_en else "",
        "realizada": bool(ev.realizada),
        "version": ev.version or 0,
    }


def _audit(db: Session, usuario, accion, periodo, anterior=None, nuevo=None, detalle=None):
    db.add(AssessmentAudit(
        usuario=usuario, accion=accion, periodo=periodo,
        valor_anterior=anterior, valor_nuevo=nuevo, detalle=detalle, fecha=utc_now(),
    ))


# ============================================================
# BOOTSTRAP  (session + periods + employees + evaluators)
# ============================================================

@router.get("/bootstrap")
async def bootstrap(
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
    period: Optional[str] = None,
):
    user = await _get_current_user(authorization, db)
    periodo = period or _active_period_id(db)
    if not periodo:
        raise HTTPException(status_code=404, detail="No assessment period configured")

    ctx = _build_context(db, user, periodo)

    periods = db.query(AssessmentPeriod).order_by(AssessmentPeriod.id.desc()).all()
    meta = _employee_meta(db)

    # Employees visible to this user
    empleados = [
        meta[c] for c in meta
        if ctx.can_see(c)
    ]
    empleados.sort(key=lambda e: e["codigo"])

    # Evaluator assignments for the period (visible scope)
    evald = db.query(AssessmentEvaluator).filter(AssessmentEvaluator.periodo == periodo).all()
    evaluadores = [
        {"codigo": e.codigo, "periodo": e.periodo, "evaluador": e.evaluador}
        for e in evald if ctx.can_see(e.codigo)
    ]

    return {
        "session": {
            "nc_user_id": user.nc_user_id,
            "nombre": ctx.nombre,
            "role": ctx.role,
            "codigo": ctx.codigo,
            "visibleCodes": sorted(ctx.visible_codes) if ctx.visible_codes is not None else None,
        },
        "periodos": [{"id": p.id, "nombre": p.nombre, "estado": p.estado} for p in periods],
        "periodoActivo": _active_period_id(db),
        "empleados": empleados,
        "evaluadores": evaluadores,
        "ts": int(datetime.now().timestamp() * 1000),
    }


# ============================================================
# EVALUATIONS
# ============================================================

@router.get("/evaluations")
async def list_evaluations(
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
    period: Optional[str] = None,
):
    user = await _get_current_user(authorization, db)
    periodo = period or _active_period_id(db)
    ctx = _build_context(db, user, periodo)
    meta = _employee_meta(db)

    rows = db.query(AssessmentEvaluation).filter(AssessmentEvaluation.periodo == periodo).all()
    out = []
    for ev in rows:
        if not ctx.can_see(ev.codigo):
            continue
        evaluador = _assigned_evaluator(db, ev.codigo, periodo)
        out.append(_serialize_eval(ev, meta.get(ev.codigo, {}), evaluador))
    return {"evaluaciones": out, "ts": int(datetime.now().timestamp() * 1000)}


@router.post("/evaluations")
async def save_evaluation(
    body: EvaluationIn,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_context(db, user, body.periodo)

    if not ctx.can_see(body.codigo):
        raise HTTPException(status_code=403, detail="No access to this evaluation")

    emp = db.query(AssessmentEmployee).filter(AssessmentEmployee.codigo == body.codigo).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    period = db.query(AssessmentPeriod).filter(AssessmentPeriod.id == body.periodo).first()
    if not period:
        raise HTTPException(status_code=404, detail="Period not found")
    if period.estado == "cerrado" and not ctx.is_admin():
        raise HTTPException(status_code=403, detail="Period is closed")

    eval_id = f"EV_{body.codigo}_{body.periodo.replace('-', '')}"
    ev = db.query(AssessmentEvaluation).filter(AssessmentEvaluation.id == eval_id).first()
    is_new = ev is None

    # Who is this user relative to the record?
    assigned = _assigned_evaluator(db, body.codigo, body.periodo)
    is_subject = (ctx.codigo == body.codigo)
    is_evaluator = ctx.is_admin() or (assigned is not None and assigned == ctx.nombre) or \
        (ctx.role == "leader" and ctx.can_see(body.codigo) and not is_subject)

    if not is_subject and not is_evaluator:
        raise HTTPException(status_code=403, detail="Not allowed to edit this evaluation")

    prev_estado = ev.estado_eval if ev else "Borrador"

    # Locked records: only admin or the assigned evaluator may touch them (to reopen).
    if prev_estado in ("Enviada", "Cerrada"):
        reopening = (body.estadoEval == "Borrador")
        if not (ctx.is_admin() or is_evaluator):
            raise HTTPException(status_code=403, detail="Evaluation is locked")
        if not reopening and not ctx.is_admin():
            raise HTTPException(status_code=403, detail="Evaluation is locked")

    if is_new:
        ev = AssessmentEvaluation(
            id=eval_id, codigo=body.codigo, periodo=body.periodo,
            competencias=[], kpis_detalle=[], plan={}, version=0,
            estado_eval="Borrador", kpi=0, politicas=0,
        )
        db.add(ev)

    # ── Merge competencias element-wise according to role ──────────────────────
    existing = list(ev.competencias or [])
    incoming = list(body.competencias or [])
    n = max(len(existing), len(incoming))
    merged = []
    for i in range(n):
        cur = existing[i] if i < len(existing) else {"self": 0, "lead": 0}
        new = incoming[i] if i < len(incoming) else {}
        self_v = cur.get("self", 0)
        lead_v = cur.get("lead", 0)
        if is_subject:
            self_v = new.get("self", self_v)
        if is_evaluator:
            lead_v = new.get("lead", lead_v)
        merged.append({"self": self_v, "lead": lead_v})
    ev.competencias = merged

    # ── Evaluator-only scalar fields ───────────────────────────────────────────
    if is_evaluator:
        ev.evaluador = body.evaluador or ev.evaluador
        ev.fecha = body.fecha if body.fecha is not None else ev.fecha
        ev.politicas = Decimal(str(body.politicas))
        ev.kpi = Decimal(str(body.kpi))
        ev.kpis_detalle = body.kpisDetalle or []
        ev.fortalezas = body.fortalezas or ""
        ev.oportunidades = body.oportunidades or ""
        ev.comentarios = body.comentarios or ""
        ev.plan = body.plan or {}

    # ── Estado transitions ─────────────────────────────────────────────────────
    new_estado = body.estadoEval or "Borrador"
    if new_estado != prev_estado:
        if new_estado == "Enviada" and is_subject:
            ev.estado_eval = "Enviada"
            ev.enviada_por = ctx.nombre
            ev.enviada_en = utc_now()
            _audit(db, ctx.nombre, "Envío de evaluación", body.periodo,
                   prev_estado, "Enviada", f"Colaborador {body.codigo}")
        elif new_estado == "Borrador" and (ctx.is_admin() or is_evaluator):
            ev.estado_eval = "Borrador"
            _audit(db, ctx.nombre, "Reapertura de evaluación", body.periodo,
                   prev_estado, "Borrador", f"Colaborador {body.codigo}")
        elif new_estado == "Cerrada" and ctx.is_admin():
            ev.estado_eval = "Cerrada"
            _audit(db, ctx.nombre, "Cierre de evaluación", body.periodo,
                   prev_estado, "Cerrada", f"Colaborador {body.codigo}")
        # otherwise ignore disallowed transition (keep prev)

    ev.realizada = bool(body.realizada)
    ev.version = (ev.version or 0) + 1
    ev.updated_at = utc_now()

    # ── Immutable snapshot ─────────────────────────────────────────────────────
    meta = _employee_meta(db).get(body.codigo, {})
    snap = _serialize_eval(ev, meta, assigned)
    db.add(AssessmentVersion(
        eval_id=ev.id, codigo=ev.codigo, periodo=ev.periodo,
        version=ev.version, snapshot=snap, snapshot_at=utc_now(),
    ))

    try:
        db.commit()
        db.refresh(ev)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    return _serialize_eval(ev, meta, assigned)


@router.get("/evaluations/{codigo}/versions")
async def list_versions(
    codigo: str,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
    period: Optional[str] = None,
):
    user = await _get_current_user(authorization, db)
    periodo = period or _active_period_id(db)
    ctx = _build_context(db, user, periodo)
    if not ctx.can_see(codigo):
        raise HTTPException(status_code=403, detail="No access")
    rows = (
        db.query(AssessmentVersion)
        .filter(AssessmentVersion.codigo == codigo)
        .order_by(AssessmentVersion.vid.desc())
        .all()
    )
    return {"versiones": [
        {"vid": r.vid, "version": r.version, "snapshotAt": r.snapshot_at.isoformat() if r.snapshot_at else "",
         "snapshot": r.snapshot}
        for r in rows
    ]}


# ============================================================
# PERIODS  (admin)
# ============================================================

def _require_admin(ctx: Context):
    if not ctx.is_admin():
        raise HTTPException(status_code=403, detail="Admin only")


@router.get("/periods")
async def list_periods(
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    _build_context(db, user, _active_period_id(db))  # ensures access
    rows = db.query(AssessmentPeriod).order_by(AssessmentPeriod.id.desc()).all()
    return {
        "periodos": [{"id": p.id, "nombre": p.nombre, "estado": p.estado} for p in rows],
        "periodoActivo": _active_period_id(db),
    }


@router.post("/periods")
async def create_period(
    body: PeriodIn,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_context(db, user, _active_period_id(db))
    _require_admin(ctx)

    if db.query(AssessmentPeriod).filter(AssessmentPeriod.id == body.id).first():
        raise HTTPException(status_code=409, detail="Period already exists")
    db.add(AssessmentPeriod(id=body.id, nombre=body.nombre, estado=body.estado or "inactivo",
                            created_at=utc_now(), updated_at=utc_now()))
    _audit(db, ctx.nombre, "Creación de período", body.id, "", body.id, "")
    db.commit()
    return {"success": True, "id": body.id}


@router.post("/periods/{period_id}/activate")
async def activate_period(
    period_id: str,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_context(db, user, _active_period_id(db))
    _require_admin(ctx)

    target = db.query(AssessmentPeriod).filter(AssessmentPeriod.id == period_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Period not found")
    prev = _active_period_id(db)
    for p in db.query(AssessmentPeriod).all():
        if p.id == period_id:
            p.estado = "activo"
        elif p.estado == "activo":
            p.estado = "inactivo"
    _audit(db, ctx.nombre, "Cambio de período", period_id, prev or "", period_id, "Activación")
    db.commit()
    return {"success": True, "periodoActivo": period_id}


@router.post("/periods/{period_id}/close")
async def close_period(
    period_id: str,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_context(db, user, _active_period_id(db))
    _require_admin(ctx)

    p = db.query(AssessmentPeriod).filter(AssessmentPeriod.id == period_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Period not found")
    p.estado = "cerrado"
    _audit(db, ctx.nombre, "Cierre de período", period_id, "activo", "cerrado", "")
    db.commit()
    return {"success": True}


# ============================================================
# EVALUATORS  (admin)
# ============================================================

@router.get("/evaluators")
async def list_evaluators(
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
    period: Optional[str] = None,
):
    user = await _get_current_user(authorization, db)
    periodo = period or _active_period_id(db)
    ctx = _build_context(db, user, periodo)
    rows = db.query(AssessmentEvaluator).filter(AssessmentEvaluator.periodo == periodo).all()
    return {"evaluadores": [
        {"codigo": e.codigo, "periodo": e.periodo, "evaluador": e.evaluador}
        for e in rows if ctx.can_see(e.codigo)
    ]}


@router.post("/evaluators")
async def assign_evaluator(
    body: EvaluatorIn,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_context(db, user, body.periodo)
    _require_admin(ctx)

    assign_id = f"AS_{body.codigo}_{body.periodo.replace('-', '')}"
    row = db.query(AssessmentEvaluator).filter(AssessmentEvaluator.id == assign_id).first()
    prev = row.evaluador if row else None
    if row:
        row.evaluador_anterior = prev
        row.evaluador = body.evaluador
        row.usuario_cambio = ctx.nombre
        row.actualizado = utc_now()
    else:
        db.add(AssessmentEvaluator(
            id=assign_id, codigo=body.codigo, periodo=body.periodo,
            evaluador=body.evaluador, evaluador_anterior=prev,
            usuario_cambio=ctx.nombre, actualizado=utc_now(),
        ))
    _audit(db, ctx.nombre, "Cambio de evaluador", body.periodo,
           prev or "(ninguno)", body.evaluador, f"Colaborador {body.codigo}")
    db.commit()
    return {"success": True}


# ============================================================
# AUDIT  (admin read)
# ============================================================

@router.get("/audit")
async def list_audit(
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
    period: Optional[str] = None,
):
    user = await _get_current_user(authorization, db)
    ctx = _build_context(db, user, period or _active_period_id(db))
    _require_admin(ctx)
    q = db.query(AssessmentAudit)
    if period:
        q = q.filter(AssessmentAudit.periodo == period)
    rows = q.order_by(AssessmentAudit.aid.desc()).all()
    return {"auditoria": [
        {
            "fecha": r.fecha.isoformat() if r.fecha else "",
            "usuario": r.usuario or "",
            "accion": r.accion or "",
            "periodo": r.periodo or "",
            "valorAnterior": r.valor_anterior or "",
            "valorNuevo": r.valor_nuevo or "",
            "detalle": r.detalle or "",
        }
        for r in rows
    ]}
