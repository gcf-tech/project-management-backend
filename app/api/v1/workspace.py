"""
Workspace (Oficina virtual "Habbo") API endpoints.

Reemplaza el backend Supabase del proyecto `workspace (habbo)`. Cada endpoint mapea
1:1 a una función del antiguo `public/auth.js`. La identidad SIEMPRE se resuelve del
token Nextcloud (no del body), salvo los ids de recurso indicados.

Notas:
- Acceso abierto: cualquier usuario autenticado en Nextcloud entra (auto-provisión).
- "Gerente" (users.workspace_manager o role=admin) gatea SOLO el dashboard de equipo
  y la gestión de puestos.
- La entrega en vivo del chat la hace el WebSocket propio del workspace; aquí solo se
  persiste y se sirve el historial.
"""
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from sqlalchemy.dialects.mysql import insert as mysql_insert
from typing import Annotated, List, Optional
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pydantic import BaseModel

from app.api.dependencies import get_db
from app.core.security import get_nc_user_info
from app.core.datetime_utils import utc_now, to_rfc3339_z
from app.services.nextcloud_svc import sync_user_from_nextcloud
from app.db.models import (
    User, WorkspaceProfile, WorkspaceSession, WorkspaceDailyTime,
    WorkspaceActivity, WorkspaceTask, WorkspaceMessage, WorkspaceWorkstation,
)

router = APIRouter()

# GCF opera en Colombia (UTC-5). El "día de negocio" se calcula en hora local para
# que los minutos de la tarde no se partan en dos filas al cruzar la medianoche UTC.
BOGOTA = ZoneInfo("America/Bogota")


def business_today() -> date:
    return datetime.now(BOGOTA).date()


# ============================================================
# SCHEMAS
# ============================================================

class PerfilPatch(BaseModel):
    empresa: Optional[str] = None
    departamento: Optional[str] = None
    avatar: Optional[dict] = None
    onboarded: Optional[bool] = None


class TrabajoPatch(BaseModel):
    proyecto: Optional[str] = None
    rendimiento: Optional[int] = None
    estado: Optional[str] = None


class SumarMinutosIn(BaseModel):
    minutos: int = 1


class ActividadIn(BaseModel):
    actividad: str


class TareaIn(BaseModel):
    texto: str
    fecha: Optional[date] = None


class MarcarTareaIn(BaseModel):
    completada: bool


class MensajeIn(BaseModel):
    paraId: int
    texto: str


class CrearPuestoIn(BaseModel):
    deptId: str
    x: int = 0
    y: int = 0
    etiqueta: Optional[str] = None


class AsignarPuestoIn(BaseModel):
    usuarioId: Optional[int] = None   # null libera el puesto


class MoverPuestoIn(BaseModel):
    x: int
    y: int


# ============================================================
# HELPERS
# ============================================================

async def _resolve_user(authorization: str, db: Session) -> User:
    """Valida el token contra Nextcloud y auto-provisiona el usuario (primer login)."""
    nc_data = await get_nc_user_info(authorization)
    return await sync_user_from_nextcloud(db, nc_data, authorization)


def _is_manager(user: User) -> bool:
    return bool(user.workspace_manager) or user.role == "admin"


def _get_or_create_profile(db: Session, user_id: int) -> WorkspaceProfile:
    prof = db.query(WorkspaceProfile).filter(WorkspaceProfile.user_id == user_id).first()
    if not prof:
        prof = WorkspaceProfile(user_id=user_id, onboarded=False)
        db.add(prof)
        db.commit()
        db.refresh(prof)
    return prof


def _perfil_dict(u: User, p: Optional[WorkspaceProfile]) -> dict:
    """Forma idéntica al antiguo `perfiles` de Supabase (snake_case) para que el
    cliente (app.js/login.js) no cambie."""
    return {
        "id": u.id,
        "nombre": u.display_name,
        "email": u.email,
        "cargo": u.job_title,
        "empresa": p.empresa if p else None,
        "departamento": p.departamento if p else None,
        "avatar": p.avatar if p else None,
        "es_gerente": _is_manager(u),
        "ultima_actividad": p.ultima_actividad if p else None,
        "ultima_actividad_en": to_rfc3339_z(p.ultima_actividad_en) if (p and p.ultima_actividad_en) else None,
        "proyecto": p.proyecto if p else None,
        "rendimiento": p.rendimiento if p else None,
        "estado": p.estado if p else None,
        "onboarded": bool(p.onboarded) if p else False,
    }


def _tarea_dict(t: WorkspaceTask) -> dict:
    return {
        "id": t.id,
        "usuario_id": t.user_id,
        "texto": t.texto,
        "completada": bool(t.completada),
        "fecha": t.fecha.isoformat() if t.fecha else None,
        "creado_en": to_rfc3339_z(t.creado_en),
    }


def _msg_dict(m: WorkspaceMessage) -> dict:
    return {
        "id": m.id,
        "de_id": m.de_id,
        "para_id": m.para_id,
        "texto": m.texto,
        "creado_en": to_rfc3339_z(m.creado_en),
    }


def _puesto_dict(pu: WorkspaceWorkstation) -> dict:
    owner = pu.ocupante
    return {
        "id": pu.id,
        "dept_id": pu.dept_id,
        "x": pu.x,
        "y": pu.y,
        "usuario_id": pu.usuario_id,
        "etiqueta": pu.etiqueta,
        "dueno_nombre": owner.display_name if owner else None,
        "dueno_cargo": owner.job_title if owner else None,
    }


def _minutos_hoy(db: Session, user_id: int, dia: date) -> int:
    row = db.query(WorkspaceDailyTime).filter(
        WorkspaceDailyTime.user_id == user_id,
        WorkspaceDailyTime.fecha == dia,
    ).first()
    return row.minutos if row else 0


# ============================================================
# PERFIL
# ============================================================

@router.get("/perfil/me")
async def get_mi_perfil(authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    user = await _resolve_user(authorization, db)
    prof = _get_or_create_profile(db, user.id)
    return _perfil_dict(user, prof)


@router.get("/perfil/{user_id}")
async def get_perfil(user_id: int, authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    await _resolve_user(authorization, db)  # solo valida token
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    prof = db.query(WorkspaceProfile).filter(WorkspaceProfile.user_id == user_id).first()
    return _perfil_dict(u, prof)


@router.patch("/perfil/me")
async def patch_mi_perfil(body: PerfilPatch, authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    user = await _resolve_user(authorization, db)
    prof = _get_or_create_profile(db, user.id)
    data = body.model_dump(exclude_unset=True)
    for field in ("empresa", "departamento", "avatar", "onboarded"):
        if field in data:
            setattr(prof, field, data[field])
    db.commit()
    db.refresh(prof)
    return _perfil_dict(user, prof)


@router.patch("/perfil/me/trabajo")
async def patch_mi_trabajo(body: TrabajoPatch, authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    user = await _resolve_user(authorization, db)
    prof = _get_or_create_profile(db, user.id)
    data = body.model_dump(exclude_unset=True)
    for field in ("proyecto", "rendimiento", "estado"):
        if field in data:
            setattr(prof, field, data[field])
    db.commit()
    db.refresh(prof)
    return _perfil_dict(user, prof)


@router.get("/perfil/{user_id}/es-gerente")
async def get_es_gerente(user_id: int, authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    await _resolve_user(authorization, db)
    u = db.query(User).filter(User.id == user_id).first()
    return {"esGerente": _is_manager(u) if u else False}


@router.get("/empleados")
async def listar_empleados(authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    await _resolve_user(authorization, db)
    rows = (
        db.query(User, WorkspaceProfile)
        .outerjoin(WorkspaceProfile, WorkspaceProfile.user_id == User.id)
        .filter(User.is_active == True)  # noqa: E712
        .order_by(User.display_name.asc())
        .all()
    )
    return [
        {
            "id": u.id,
            "nombre": u.display_name,
            "email": u.email,
            "cargo": u.job_title,
            "departamento": p.departamento if p else None,
        }
        for u, p in rows
    ]


# ============================================================
# TIEMPO
# ============================================================

@router.post("/sesiones")
async def iniciar_sesion(authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    user = await _resolve_user(authorization, db)
    # cierra defensivamente cualquier sesión abierta previa
    db.query(WorkspaceSession).filter(
        WorkspaceSession.user_id == user.id,
        WorkspaceSession.fin.is_(None),
    ).update({"fin": utc_now()})
    s = WorkspaceSession(user_id=user.id, inicio=utc_now())
    db.add(s)
    db.commit()
    db.refresh(s)
    return {"sessionId": s.id}


@router.post("/sesiones/{session_id}/cerrar")
async def cerrar_sesion(session_id: int, authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    user = await _resolve_user(authorization, db)
    s = db.query(WorkspaceSession).filter(
        WorkspaceSession.id == session_id,
        WorkspaceSession.user_id == user.id,
    ).first()
    if s and s.fin is None:
        s.fin = utc_now()
        db.commit()
    return {"ok": True}


@router.post("/tiempo/sumar")
async def sumar_minutos(body: SumarMinutosIn, authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    user = await _resolve_user(authorization, db)
    hoy = business_today()
    now = utc_now()
    stmt = mysql_insert(WorkspaceDailyTime).values(
        user_id=user.id, fecha=hoy, minutos=body.minutos,
        created_at=now, updated_at=now,
    ).on_duplicate_key_update(
        minutos=WorkspaceDailyTime.__table__.c.minutos + body.minutos,
        updated_at=now,
    )
    db.execute(stmt)
    db.commit()
    return {"minutos": _minutos_hoy(db, user.id, hoy)}


@router.get("/tiempo/hoy")
async def tiempo_hoy(authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    user = await _resolve_user(authorization, db)
    return {"minutos": _minutos_hoy(db, user.id, business_today())}


@router.get("/tiempo/historial")
async def historial_tiempo(authorization: Annotated[str, Header()], dias: int = 30, db: Session = Depends(get_db)):
    user = await _resolve_user(authorization, db)
    rows = (
        db.query(WorkspaceDailyTime)
        .filter(WorkspaceDailyTime.user_id == user.id)
        .order_by(WorkspaceDailyTime.fecha.desc())
        .limit(dias)
        .all()
    )
    return [{"fecha": r.fecha.isoformat(), "minutos": r.minutos} for r in rows]


@router.get("/tiempo/mes")
async def tiempo_mes(authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    user = await _resolve_user(authorization, db)
    hoy = business_today()
    primero = hoy.replace(day=1)
    total = (
        db.query(func.coalesce(func.sum(WorkspaceDailyTime.minutos), 0))
        .filter(
            WorkspaceDailyTime.user_id == user.id,
            WorkspaceDailyTime.fecha >= primero,
        )
        .scalar()
    )
    return {"minutos": int(total or 0)}


# ============================================================
# ACTIVIDAD
# ============================================================

@router.post("/actividades")
async def reportar_actividad(body: ActividadIn, authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    user = await _resolve_user(authorization, db)
    now = utc_now()
    act = WorkspaceActivity(user_id=user.id, actividad=body.actividad, momento=now)
    db.add(act)
    prof = _get_or_create_profile(db, user.id)
    prof.ultima_actividad = body.actividad
    prof.ultima_actividad_en = now
    db.commit()
    db.refresh(act)
    return {"id": act.id, "momento": to_rfc3339_z(act.momento)}


@router.get("/resumen/{user_id}")
async def resumen_del_dia(user_id: int, authorization: Annotated[str, Header()], fecha: date, db: Session = Depends(get_db)):
    user = await _resolve_user(authorization, db)
    # el gerente puede ver el día de cualquiera; el resto solo el suyo
    if not _is_manager(user) and user.id != user_id:
        raise HTTPException(status_code=403, detail="Sin permiso")
    minutos = _minutos_hoy(db, user_id, fecha)
    acts = (
        db.query(WorkspaceActivity)
        .filter(
            WorkspaceActivity.user_id == user_id,
            func.date(WorkspaceActivity.momento) == fecha,
        )
        .order_by(WorkspaceActivity.momento.desc())
        .all()
    )
    tareas = (
        db.query(WorkspaceTask)
        .filter(WorkspaceTask.user_id == user_id, WorkspaceTask.fecha == fecha)
        .order_by(WorkspaceTask.creado_en.asc())
        .all()
    )
    return {
        "minutos": minutos,
        "actividades": [{"actividad": a.actividad, "momento": to_rfc3339_z(a.momento)} for a in acts],
        "tareas": [_tarea_dict(t) for t in tareas],
    }


# ============================================================
# TAREAS
# ============================================================

@router.get("/tareas")
async def listar_tareas(authorization: Annotated[str, Header()], fecha: Optional[date] = None, db: Session = Depends(get_db)):
    user = await _resolve_user(authorization, db)
    q = db.query(WorkspaceTask).filter(WorkspaceTask.user_id == user.id)
    if fecha:
        q = q.filter(WorkspaceTask.fecha == fecha)
    tareas = q.order_by(WorkspaceTask.creado_en.asc()).all()
    return [_tarea_dict(t) for t in tareas]


@router.post("/tareas")
async def crear_tarea(body: TareaIn, authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    user = await _resolve_user(authorization, db)
    t = WorkspaceTask(
        user_id=user.id,
        texto=body.texto,
        fecha=body.fecha or business_today(),
        creado_en=utc_now(),
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return _tarea_dict(t)


@router.patch("/tareas/{tarea_id}")
async def marcar_tarea(tarea_id: int, body: MarcarTareaIn, authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    user = await _resolve_user(authorization, db)
    t = db.query(WorkspaceTask).filter(WorkspaceTask.id == tarea_id, WorkspaceTask.user_id == user.id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    t.completada = body.completada
    db.commit()
    db.refresh(t)
    return _tarea_dict(t)


@router.delete("/tareas/{tarea_id}")
async def borrar_tarea(tarea_id: int, authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    user = await _resolve_user(authorization, db)
    t = db.query(WorkspaceTask).filter(WorkspaceTask.id == tarea_id, WorkspaceTask.user_id == user.id).first()
    if t:
        db.delete(t)
        db.commit()
    return {"ok": True}


# ============================================================
# EQUIPO (gerente)
# ============================================================

@router.get("/equipo")
async def datos_equipo(authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    user = await _resolve_user(authorization, db)
    if not _is_manager(user):
        raise HTTPException(status_code=403, detail="Solo gerentes")
    hoy = business_today()
    minutos_map = dict(
        db.query(WorkspaceDailyTime.user_id, WorkspaceDailyTime.minutos)
        .filter(WorkspaceDailyTime.fecha == hoy)
        .all()
    )
    rows = (
        db.query(User, WorkspaceProfile)
        .outerjoin(WorkspaceProfile, WorkspaceProfile.user_id == User.id)
        .filter(User.is_active == True)  # noqa: E712
        .all()
    )
    return [{**_perfil_dict(u, p), "minutosHoy": minutos_map.get(u.id, 0)} for u, p in rows]


@router.get("/equipo/{user_id}")
async def ficha_usuario(user_id: int, authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    user = await _resolve_user(authorization, db)
    if not _is_manager(user) and user.id != user_id:
        raise HTTPException(status_code=403, detail="Sin permiso")
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    prof = db.query(WorkspaceProfile).filter(WorkspaceProfile.user_id == user_id).first()
    tareas = (
        db.query(WorkspaceTask)
        .filter(WorkspaceTask.user_id == user_id)
        .order_by(WorkspaceTask.creado_en.asc())
        .all()
    )
    return {
        "perfil": _perfil_dict(u, prof),
        "tareas": [_tarea_dict(t) for t in tareas],
        "minutosHoy": _minutos_hoy(db, user_id, business_today()),
    }


# ============================================================
# MENSAJES (persistencia + historial; entrega en vivo por WebSocket)
# ============================================================

@router.post("/mensajes")
async def enviar_mensaje(body: MensajeIn, authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    user = await _resolve_user(authorization, db)  # de_id = caller (nunca del body)
    m = WorkspaceMessage(de_id=user.id, para_id=body.paraId, texto=body.texto, creado_en=utc_now())
    db.add(m)
    db.commit()
    db.refresh(m)
    return _msg_dict(m)


@router.get("/mensajes/{otro_id}")
async def leer_conversacion(otro_id: int, authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    user = await _resolve_user(authorization, db)
    yo = user.id
    msgs = (
        db.query(WorkspaceMessage)
        .filter(
            or_(
                and_(WorkspaceMessage.de_id == yo, WorkspaceMessage.para_id == otro_id),
                and_(WorkspaceMessage.de_id == otro_id, WorkspaceMessage.para_id == yo),
            )
        )
        .order_by(WorkspaceMessage.creado_en.asc())
        .all()
    )
    return [_msg_dict(m) for m in msgs]


# ============================================================
# PUESTOS
# ============================================================

@router.get("/puestos")
async def listar_puestos(authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    await _resolve_user(authorization, db)
    puestos = db.query(WorkspaceWorkstation).all()
    return [_puesto_dict(p) for p in puestos]


@router.get("/puestos/mio")
async def mi_puesto(authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    user = await _resolve_user(authorization, db)
    pu = db.query(WorkspaceWorkstation).filter(WorkspaceWorkstation.usuario_id == user.id).first()
    return _puesto_dict(pu) if pu else None


@router.post("/puestos")
async def crear_puesto(body: CrearPuestoIn, authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    user = await _resolve_user(authorization, db)
    if not _is_manager(user):
        raise HTTPException(status_code=403, detail="Solo gerentes")
    pu = WorkspaceWorkstation(dept_id=body.deptId, x=body.x, y=body.y, etiqueta=body.etiqueta)
    db.add(pu)
    db.commit()
    db.refresh(pu)
    return _puesto_dict(pu)


@router.patch("/puestos/{puesto_id}/asignar")
async def asignar_puesto(puesto_id: int, body: AsignarPuestoIn, authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    user = await _resolve_user(authorization, db)
    if not _is_manager(user):
        raise HTTPException(status_code=403, detail="Solo gerentes")
    pu = db.query(WorkspaceWorkstation).filter(WorkspaceWorkstation.id == puesto_id).first()
    if not pu:
        raise HTTPException(status_code=404, detail="Puesto no encontrado")
    if body.usuarioId:
        # un usuario ocupa como máximo un puesto: libera cualquier otro que tuviera
        db.query(WorkspaceWorkstation).filter(
            WorkspaceWorkstation.usuario_id == body.usuarioId,
            WorkspaceWorkstation.id != puesto_id,
        ).update({"usuario_id": None})
    pu.usuario_id = body.usuarioId
    db.commit()
    db.refresh(pu)
    return _puesto_dict(pu)


@router.patch("/puestos/{puesto_id}/mover")
async def mover_puesto(puesto_id: int, body: MoverPuestoIn, authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    user = await _resolve_user(authorization, db)
    if not _is_manager(user):
        raise HTTPException(status_code=403, detail="Solo gerentes")
    pu = db.query(WorkspaceWorkstation).filter(WorkspaceWorkstation.id == puesto_id).first()
    if not pu:
        raise HTTPException(status_code=404, detail="Puesto no encontrado")
    pu.x = body.x
    pu.y = body.y
    db.commit()
    db.refresh(pu)
    return _puesto_dict(pu)


@router.delete("/puestos/{puesto_id}")
async def borrar_puesto(puesto_id: int, authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    user = await _resolve_user(authorization, db)
    if not _is_manager(user):
        raise HTTPException(status_code=403, detail="Solo gerentes")
    pu = db.query(WorkspaceWorkstation).filter(WorkspaceWorkstation.id == puesto_id).first()
    if pu:
        db.delete(pu)
        db.commit()
    return {"ok": True}
