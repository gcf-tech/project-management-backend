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
import re
import urllib.parse
import httpx
from fastapi import APIRouter, Depends, HTTPException, Header, Response, File, UploadFile
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from sqlalchemy.dialects.mysql import insert as mysql_insert
from typing import Annotated, List, Optional
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo
from pydantic import BaseModel

from app.api.dependencies import get_db
from app.core.config import NC_URL
from app.core.security import get_nc_user_info
from app.core.datetime_utils import utc_now, to_rfc3339_z, ensure_aware_utc, UTC
from app.services.nextcloud_svc import sync_user_from_nextcloud
from app.services.email_svc import send_email
from app.db.models import (
    User, WorkspaceProfile, WorkspaceSession, WorkspaceDailyTime,
    WorkspaceActivity, WorkspaceTask, WorkspaceMessage, WorkspaceWorkstation,
    WorkspaceMeeting, WorkspaceMeetingParticipant, WorkspaceNews,
    WorkspacePushSubscription,
    DeckCard, DeckCardAssignee, DeckColumn,
)
from app.core import config as _cfg
from app.services.push import enviar_push, push_habilitado
from app.services.push_tokens import guardar_access_token, cifrado_disponible

router = APIRouter()

# GCF opera en Colombia (UTC-5). El "día de negocio" se calcula en hora local para
# que los minutos de la tarde no se partan en dos filas al cruzar la medianoche UTC.
BOGOTA = ZoneInfo("America/Bogota")


def business_today() -> date:
    return datetime.now(BOGOTA).date()


# Equipo del Deck → oficina del workspace (id de oficina, etiqueta visible).
TEAM_OFICINA = {
    "tech": ("tech", "Tecnología"),
    "commercial": ("comm", "Comercial"),
    "marketing": ("mkt", "Marketing"),
    "finance": ("fin", "Finanzas"),
    "admin": ("admin", "Administración"),
    "operaciones": ("ops", "Operaciones"),
    "criptox": ("crypto", "CryptoX"),
    "cryptox": ("crypto", "CryptoX"),
    "coordinación": ("ceo", "Dirección"),
    "coordinacion": ("ceo", "Dirección"),
}


def _oficina_de(user):
    """(id_oficina, etiqueta) según el equipo del usuario, o None."""
    t = (user.team.name if getattr(user, "team", None) else "") or ""
    return TEAM_OFICINA.get(t.lower().strip())


# ids de oficina válidos (para validar el parámetro de los tableros por oficina)
_OFICINA_IDS = {v[0] for v in TEAM_OFICINA.values()}


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


class ReunionIn(BaseModel):
    titulo: Optional[str] = None
    meetUrl: Optional[str] = None
    inicio: datetime
    fin: Optional[datetime] = None
    participantes: List[int] = []


class ReunionPatch(BaseModel):
    """Reprogramar o retitular. Los tres campos son opcionales y todos None se
    rechaza con 422: un PATCH vacío no debe pasar por bueno."""
    titulo: Optional[str] = None
    inicio: Optional[datetime] = None   # ISO con offset; se normaliza a UTC
    fin: Optional[datetime] = None


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
        "oficina": (_oficina_de(u) or (None, None))[0],        # id de su oficina (por equipo)
        "oficina_label": (_oficina_de(u) or (None, None))[1],  # nombre de su oficina
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
    d = _perfil_dict(user, prof)
    d["nc_user_id"] = user.nc_user_id  # para identificar "mis" mensajes en Talk
    return d


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


# ============================================================
# REUNIONES (persistidas — "tus reuniones de hoy")
# ============================================================

def _ical_esc(s: str) -> str:
    return str(s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fmt_ics(dt: datetime) -> str:
    """Datetime → 'YYYYMMDDTHHMMSSZ'. Un naive se toma como UTC, la misma
    convención de `to_rfc3339_z`: al reemitir el .ics los valores vienen de MySQL
    y pueden llegar sin tzinfo, y asumir la zona del servidor movería el evento
    cinco horas sin que nadie lo pidiera."""
    aware = dt if dt.tzinfo is not None and dt.utcoffset() is not None else dt.replace(tzinfo=UTC)
    return aware.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _url_evento_caldav(nc_user_id: str, ev_uid: str) -> str:
    """URL del .ics en el calendario 'personal'. Vive en UN solo sitio porque el
    borrado y la reemisión tienen que reconstruir EXACTAMENTE la URL que usó el
    alta: el UID es determinista (`gcfws-{id}`), así que no hay nada que buscar,
    pero solo mientras las tres la construyan igual.

    `safe=""` y no el `safe="/"` por defecto: una barra sin escapar en el id de
    usuario dejaría de ser un segmento del calendario propio para apuntar a otra
    ruta del servidor DAV. Para un id normal no cambia nada."""
    return (
        f"{NC_URL}/remote.php/dav/calendars/"
        f"{urllib.parse.quote(nc_user_id, safe='')}/personal/{urllib.parse.quote(ev_uid, safe='')}.ics"
    )


def _vevent_ics(ev_uid, titulo, inicio, fin, descripcion="", sequence=0) -> bytes:
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//GCF//Workspace//ES", "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT", f"UID:{ev_uid}@gcf-workspace", f"DTSTAMP:{_fmt_ics(utc_now())}",
        f"DTSTART:{_fmt_ics(inicio)}", f"DTEND:{_fmt_ics(fin)}", f"SUMMARY:{_ical_esc(titulo)}",
        f"SEQUENCE:{int(sequence)}",
    ]
    if descripcion:
        lines.append(f"DESCRIPTION:{_ical_esc(descripcion)}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


async def _put_evento_caldav(authorization, nc_user_id, ev_uid, payload) -> bool:
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.put(
            _url_evento_caldav(nc_user_id, ev_uid),
            headers={"Authorization": authorization, "Content-Type": "text/calendar; charset=utf-8"},
            content=payload,
        )
    return r.status_code in (200, 201, 204)


async def _crear_evento_caldav(authorization, nc_user_id, ev_uid, titulo, inicio, fin, descripcion=""):
    """Best-effort: crea un VEVENT en el calendario 'personal' de Nextcloud del usuario."""
    payload = _vevent_ics(ev_uid, titulo, inicio, fin, descripcion, sequence=0)
    return await _put_evento_caldav(authorization, nc_user_id, ev_uid, payload)


async def _sequence_actual_caldav(authorization, nc_user_id, ev_uid) -> int:
    """SEQUENCE del .ics que ya está en Nextcloud. iCalendar pide incrementarlo en
    cada actualización, y no hay columna donde guardarlo (este cambio no trae
    migración), así que se lee del propio recurso. Si no se puede leer → 0, que
    solo arriesga que un cliente ignore la actualización, nunca que se pierda."""
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                _url_evento_caldav(nc_user_id, ev_uid),
                headers={"Authorization": authorization},
            )
        if r.status_code != 200:
            return 0
        m = re.search(r"^SEQUENCE:(\d+)", r.text, re.MULTILINE)
        return int(m.group(1)) if m else 0
    except Exception:
        return 0


async def _actualizar_evento_caldav(authorization, nc_user_id, ev_uid, titulo, inicio, fin, descripcion=""):
    """Best-effort: reemite el .ics con los datos nuevos — mismo UID, DTSTAMP nuevo
    y SEQUENCE+1, que es lo que iCalendar entiende por 'esto es una actualización'
    y no por 'esto es otro evento'."""
    seq = await _sequence_actual_caldav(authorization, nc_user_id, ev_uid) + 1
    payload = _vevent_ics(ev_uid, titulo, inicio, fin, descripcion, sequence=seq)
    return await _put_evento_caldav(authorization, nc_user_id, ev_uid, payload)


async def _borrar_evento_caldav(authorization, nc_user_id, ev_uid) -> bool:
    """Espejo del alta: borra el .ics que creó `_crear_evento_caldav`.

    Un **404 es ÉXITO**: significa que la copia ya no está, que es exactamente lo
    que se pedía. Tratarlo como fallo haría que cancelar dos veces la misma
    reunión reportara un problema inventado.

    Nunca lanza — devuelve en qué acabó. Quien llama lo reporta en vez de
    tragárselo: tragarse este fallo en silencio es lo que dejó eventos fantasma
    en el drawer de Calendario sin fila en MySQL que los explicara."""
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.delete(
                _url_evento_caldav(nc_user_id, ev_uid),
                headers={"Authorization": authorization},
            )
    except Exception:
        return False
    return r.status_code in (200, 202, 204, 404)


@router.post("/reuniones")
async def crear_reunion(body: ReunionIn, authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    user = await _resolve_user(authorization, db)
    m = WorkspaceMeeting(
        titulo=body.titulo,
        meet_url=body.meetUrl,
        inicio=body.inicio,
        fin=body.fin,
        creador_id=user.id,
        created_at=utc_now(),
    )
    db.add(m)
    db.flush()
    # participantes internos + el creador, sin duplicados; solo ids de usuarios válidos
    ids = set(body.participantes or []) | {user.id}
    validos = {u.id for u in db.query(User.id).filter(User.id.in_(ids)).all()} if ids else set()
    for uid in validos:
        db.add(WorkspaceMeetingParticipant(meeting_id=m.id, user_id=uid))
    db.commit()
    db.refresh(m)

    # También el evento en el calendario Nextcloud del organizador (best-effort).
    # SOLO el del organizador, a propósito: a los invitados el evento les llega por
    # la invitación de Google, que es la que sabe de asistentes, respuestas y Meet.
    # Escribirles además por CalDAV les dejaría DOS eventos para la misma reunión,
    # uno de ellos sin poder responder. El borrado y la reemisión son espejo de
    # esto y tocan exactamente la misma copia, ni una más.
    try:
        fin_dt = body.fin or (body.inicio + timedelta(hours=1))
        desc = ("Reunión de GCF Workspace. " + (body.meetUrl or "")).strip()
        await _crear_evento_caldav(authorization, user.nc_user_id, f"gcfws-{m.id}",
                                   body.titulo or "Reunión", body.inicio, fin_dt, desc)
    except Exception:
        pass
    return {"id": m.id}


@router.get("/reuniones/hoy")
async def reuniones_hoy(authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    user = await _resolve_user(authorization, db)
    hoy = business_today()
    ini = datetime.combine(hoy, dtime.min, tzinfo=BOGOTA).astimezone(UTC)
    fin = datetime.combine(hoy, dtime.max, tzinfo=BOGOTA).astimezone(UTC)
    rows = (
        db.query(WorkspaceMeeting)
        .join(WorkspaceMeetingParticipant, WorkspaceMeetingParticipant.meeting_id == WorkspaceMeeting.id)
        .filter(
            WorkspaceMeetingParticipant.user_id == user.id,
            WorkspaceMeeting.inicio >= ini,
            WorkspaceMeeting.inicio <= fin,
        )
        .order_by(WorkspaceMeeting.inicio.asc())
        .all()
    )
    return [
        {
            "id": m.id,
            "titulo": m.titulo,
            "meetUrl": m.meet_url,
            "inicio": to_rfc3339_z(m.inicio),
            "fin": to_rfc3339_z(m.fin),
            "esCreador": m.creador_id == user.id,
        }
        for m in rows
    ]


@router.get("/reuniones/historial")
async def reuniones_historial(authorization: Annotated[str, Header()], dias: int = 30, db: Session = Depends(get_db)):
    user = await _resolve_user(authorization, db)
    dias = max(1, min(dias, 365))
    hoy = business_today()
    inicio_hoy = datetime.combine(hoy, dtime.min, tzinfo=BOGOTA).astimezone(UTC)
    desde = datetime.combine(hoy - timedelta(days=dias), dtime.min, tzinfo=BOGOTA).astimezone(UTC)
    rows = (
        db.query(WorkspaceMeeting)
        .join(WorkspaceMeetingParticipant, WorkspaceMeetingParticipant.meeting_id == WorkspaceMeeting.id)
        .filter(
            WorkspaceMeetingParticipant.user_id == user.id,
            WorkspaceMeeting.inicio < inicio_hoy,   # anteriores a hoy (hoy va en /reuniones/hoy)
            WorkspaceMeeting.inicio >= desde,
        )
        .order_by(WorkspaceMeeting.inicio.desc())
        .limit(50)
        .all()
    )
    ids = [m.id for m in rows]
    counts = {}
    if ids:
        for mid, cnt in (
            db.query(WorkspaceMeetingParticipant.meeting_id, func.count())
            .filter(WorkspaceMeetingParticipant.meeting_id.in_(ids))
            .group_by(WorkspaceMeetingParticipant.meeting_id)
            .all()
        ):
            counts[mid] = cnt
    return [
        {
            "id": m.id,
            "titulo": m.titulo,
            "inicio": to_rfc3339_z(m.inicio),
            "fin": to_rfc3339_z(m.fin),
            "esCreador": m.creador_id == user.id,
            "participantes": counts.get(m.id, 0),
        }
        for m in rows
    ]


# ---- Cancelar y editar una reunión ---------------------------------------
# Un id en la ruta NO autoriza nada: las tres rutas de abajo resuelven al usuario
# del token y comprueban en el SERVIDOR qué puede hacer con esa reunión.
# Se distinguen dos negativas porque significan cosas distintas:
#   404 → esa reunión no existe PARA TI (ni la creaste ni te invitaron). No se
#         confirma que exista para otro: un id ajeno no debe poder sondearse.
#   403 → existe y la ves porque te invitaron, pero no la creaste, así que no
#         puedes cancelarla ni moverla. Devolver 404 aquí sería mentir sobre algo
#         que la persona sí tiene en su panel.

def _reunion_de(db: Session, meeting_id: int, user: User):
    """(reunión, es_participante) de una reunión que el usuario puede VER.
    Devuelve (None, False) si no existe o no es suya en ningún sentido."""
    m = db.query(WorkspaceMeeting).filter(WorkspaceMeeting.id == meeting_id).first()
    if not m:
        return None, False
    es_part = (
        db.query(WorkspaceMeetingParticipant)
        .filter(
            WorkspaceMeetingParticipant.meeting_id == meeting_id,
            WorkspaceMeetingParticipant.user_id == user.id,
        )
        .first()
        is not None
    )
    if not es_part and m.creador_id != user.id:
        return None, False
    return m, es_part


def _reunion_dict(db: Session, m: WorkspaceMeeting, user: User) -> dict:
    """Forma completa de UNA reunión. Incluye los CORREOS de los participantes
    porque quien va a cancelar necesita ver a quién le va a llegar el aviso; es
    el último punto donde se detecta que la reunión no era la que se creía."""
    correos = [
        e for (e,) in db.query(User.email)
        .join(WorkspaceMeetingParticipant, WorkspaceMeetingParticipant.user_id == User.id)
        .filter(WorkspaceMeetingParticipant.meeting_id == m.id)
        .all()
        if e
    ]
    return {
        "id": m.id,
        "titulo": m.titulo,
        "meetUrl": m.meet_url,
        "inicio": to_rfc3339_z(m.inicio),
        "fin": to_rfc3339_z(m.fin),
        "esCreador": m.creador_id == user.id,
        "invitados": sorted(correos),
    }


@router.get("/reuniones/proximas")
async def reuniones_proximas(
    authorization: Annotated[str, Header()],
    dias: int = 30,
    db: Session = Depends(get_db),
):
    """Reuniones del usuario de HOY en adelante. No existía: /reuniones/hoy y
    /reuniones/historial dejaban un hueco donde justamente cae "cancela la
    reunión de mañana", que es sobre lo que el asistente tiene que actuar."""
    user = await _resolve_user(authorization, db)
    dias = max(1, min(dias, 365))
    hoy = business_today()
    desde = datetime.combine(hoy, dtime.min, tzinfo=BOGOTA).astimezone(UTC)
    hasta = datetime.combine(hoy + timedelta(days=dias), dtime.max, tzinfo=BOGOTA).astimezone(UTC)
    rows = (
        db.query(WorkspaceMeeting)
        .join(WorkspaceMeetingParticipant, WorkspaceMeetingParticipant.meeting_id == WorkspaceMeeting.id)
        .filter(
            WorkspaceMeetingParticipant.user_id == user.id,   # filtro en el SERVIDOR
            WorkspaceMeeting.inicio >= desde,
            WorkspaceMeeting.inicio <= hasta,
        )
        .order_by(WorkspaceMeeting.inicio.asc())
        .limit(50)
        .all()
    )
    return [_reunion_dict(db, m, user) for m in rows]


@router.get("/reuniones/{meeting_id}")
async def reunion_detalle(
    meeting_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    """Una reunión concreta. La declara DESPUÉS de /hoy, /historial y /proximas
    a propósito: FastAPI resuelve por orden y {meeting_id} se tragaría "hoy"."""
    user = await _resolve_user(authorization, db)
    m, _ = _reunion_de(db, meeting_id, user)
    if not m:
        raise HTTPException(status_code=404, detail="Reunión no encontrada")
    return _reunion_dict(db, m, user)


@router.delete("/reuniones/{meeting_id}")
async def borrar_reunion(
    meeting_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    """Cancela la reunión borrando la fila (los participantes caen por CASCADE).
    Devuelve la reunión tal como estaba: quien llama necesita `meetUrl` e
    `inicio` para cancelar también el evento de Google, y leerlos de aquí evita
    confiar en los que mande el cliente.

    Borra ADEMÁS la copia CalDAV que creó `POST /reuniones`. El alta escribe en
    dos sitios (MySQL + un .ics en Nextcloud) y durante un tiempo la baja borró
    solo en uno: el evento sobrevivía en el drawer de Calendario sin ninguna fila
    que lo explicara. `caldav_borrado` dice si esta vez se limpió."""
    user = await _resolve_user(authorization, db)
    m, es_part = _reunion_de(db, meeting_id, user)
    if not m:
        raise HTTPException(status_code=404, detail="Reunión no encontrada")
    if m.creador_id != user.id:
        raise HTTPException(status_code=403, detail="Solo quien creó la reunión puede cancelarla")
    datos = _reunion_dict(db, m, user)
    # CalDAV va ANTES que MySQL. Si Nextcloud falla, la reunión sigue existiendo y
    # se puede reintentar; al revés quedaría la copia huérfana y ya sin fila desde
    # la que reintentar nada. Best-effort igual que el alta: no tumba la
    # cancelación, pero a diferencia del alta el resultado SÍ se reporta.
    caldav_borrado = await _borrar_evento_caldav(authorization, user.nc_user_id, f"gcfws-{m.id}")
    db.delete(m)
    db.commit()
    return {"ok": True, "reunion": datos, "caldav_borrado": caldav_borrado}


@router.patch("/reuniones/{meeting_id}")
async def editar_reunion(
    meeting_id: int,
    body: ReunionPatch,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    """Cambia título y/o horario. Solo el creador: mover la hora le cambia el día
    a todos los invitados.

    Reemite además el .ics de Nextcloud. Es la misma asimetría que tenía el
    borrado —el alta escribe en dos sitios y la edición actualizaba uno— pero
    estaba tapada: el evento fantasma que dejaba la cancelación se notaba antes
    que un evento con la hora vieja."""
    user = await _resolve_user(authorization, db)
    m, _ = _reunion_de(db, meeting_id, user)
    if not m:
        raise HTTPException(status_code=404, detail="Reunión no encontrada")
    if m.creador_id != user.id:
        raise HTTPException(status_code=403, detail="Solo quien creó la reunión puede editarla")

    if body.titulo is None and body.inicio is None and body.fin is None:
        raise HTTPException(status_code=422, detail="No hay nada que cambiar")

    if body.titulo is not None:
        m.titulo = body.titulo.strip()[:255]
    try:
        # Sin offset, la hora nueva se guardaría con la zona del servidor y la
        # reunión se movería cinco horas sin que nadie lo pidiera.
        if body.inicio is not None:
            m.inicio = ensure_aware_utc(body.inicio)
        if body.fin is not None:
            m.fin = ensure_aware_utc(body.fin)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    # Un fin anterior al inicio deja la reunión "terminada" antes de empezar y el
    # panel la pinta en gris para siempre: se rechaza en vez de guardarla rota.
    if m.fin is not None and m.fin <= m.inicio:
        raise HTTPException(status_code=422, detail="El fin debe ser posterior al inicio")

    db.commit()
    db.refresh(m)

    # Mismos valores que usó el alta, recalculados desde la fila ya guardada: un
    # `fin` nulo son 60 min, y la descripción vuelve a llevar el enlace de Meet.
    caldav_actualizado = False
    try:
        fin_dt = m.fin or (m.inicio + timedelta(hours=1))
        desc = ("Reunión de GCF Workspace. " + (m.meet_url or "")).strip()
        caldav_actualizado = await _actualizar_evento_caldav(
            authorization, user.nc_user_id, f"gcfws-{m.id}",
            m.titulo or "Reunión", m.inicio, fin_dt, desc,
        )
    except Exception:
        pass

    salida = _reunion_dict(db, m, user)
    salida["caldav_actualizado"] = caldav_actualizado
    return salida


# ============================================================
# DECK — tareas activas de un usuario (para la ficha de perfil in-world)
# ============================================================

@router.get("/deck-tareas/{user_id}")
async def deck_tareas(user_id: int, authorization: Annotated[str, Header()], limit: int = 3, db: Session = Depends(get_db)):
    await _resolve_user(authorization, db)  # valida el token
    base = (
        db.query(DeckCard)
        .join(DeckCardAssignee, DeckCardAssignee.card_id == DeckCard.id)
        .filter(
            DeckCardAssignee.user_id == user_id,
            DeckCard.completed_at.is_(None),
            DeckCard.archived == False,  # noqa: E712
        )
    )
    total = base.count()
    rows = base.order_by(DeckCard.updated_at.desc()).limit(min(max(limit, 1), 10)).all()
    col_ids = {c.column_id for c in rows if c.column_id}
    cols = {}
    if col_ids:
        for cid, title, color in db.query(DeckColumn.id, DeckColumn.title, DeckColumn.color).filter(DeckColumn.id.in_(col_ids)).all():
            cols[cid] = (title, color)
    cards = [
        {
            "id": c.id,
            "title": c.title,
            "priority": c.priority,
            "stage": cols.get(c.column_id, (None, None))[0],
            "stageColor": cols.get(c.column_id, (None, None))[1],
            "dueDate": to_rfc3339_z(c.due_date),
        }
        for c in rows
    ]
    return {"total": total, "cards": cards}


# ============================================================
# "LLAMAR LA ATENCIÓN" (nudge) — al acercarse a alguien: aviso in-app + correo
# ============================================================

class NudgeIn(BaseModel):
    targetUserId: int


@router.post("/nudge")
async def nudge(body: NudgeIn, authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    caller = await _resolve_user(authorization, db)
    target = db.query(User).filter(User.id == body.targetUserId).first()
    if not target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    enviado = False
    if target.email:
        quien = caller.display_name or "Un compañero"
        subject = f"👋 {quien} te llama la atención en el Workspace"
        html = f"""<!doctype html><html><body style="margin:0;background:#f4f6fa;padding:24px;font-family:Inter,Arial,sans-serif;">
  <div style="max-width:520px;margin:0 auto;background:#fff;border:1px solid #dde3ec;border-radius:14px;overflow:hidden;">
    <div style="background:#2B3242;padding:16px 22px;color:#fff;font-weight:800;font-size:16px;">GCF · Workspace</div>
    <div style="padding:22px;">
      <p style="margin:0 0 10px;font-size:15px;color:#1c2430;">Hola {target.display_name or ''},</p>
      <p style="margin:0 0 12px;font-size:15px;color:#1c2430;"><b>{quien}</b> te está llamando la atención en el Workspace 👋</p>
      <a href="https://workspace.gcf.group" style="display:inline-block;margin-top:8px;background:#C4641A;color:#fff;text-decoration:none;font-weight:700;padding:10px 18px;border-radius:9px;font-size:14px;">Entrar al Workspace</a>
    </div>
    <div style="padding:14px 22px;border-top:1px solid #eef1f6;color:#8a93a3;font-size:12px;">Notificación automática · no respondas a este correo.</div>
  </div></body></html>"""
        text = f"{quien} te está llamando la atención en el Workspace. Entra: https://workspace.gcf.group"
        try:
            enviado = await send_email(target.email, subject, html, text)
        except Exception:
            enviado = False
    return {"ok": True, "email": bool(enviado)}


# ============================================================
# NEXTCLOUD TALK (chat unificado) — proxy con el token del usuario
# ============================================================
# La UI de Talk no se puede embeber (X-Frame cross-subdominio), pero su API OCS sí
# funciona con el token OAuth del usuario (misma auth que /cloud/user). El chat del
# workspace se apoya en Talk: los mensajes viven en Nextcloud (móvil/escritorio/web).

_TALK = f"{NC_URL}/ocs/v2.php/apps/spreed"


class MensajeTalkIn(BaseModel):
    message: str
    replyTo: Optional[int] = None


class OneToOneIn(BaseModel):
    userId: int


async def _talk(method: str, path: str, authorization: str, *, params=None, data=None):
    headers = {"Authorization": authorization, "OCS-APIRequest": "true", "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=40.0) as client:
        resp = await client.request(method, f"{_TALK}{path}", headers=headers, params=params, data=data)
    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail="Token inválido para Talk")
    # 304 Not Modified / cuerpo vacío = sin novedades (típico del sondeo de chat)
    if resp.status_code == 304 or not resp.content:
        return []
    try:
        payload = resp.json()
    except Exception:
        raise HTTPException(status_code=502, detail="Respuesta no-JSON de Talk")
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=f"Talk error: {resp.text[:200]}")
    return payload.get("ocs", {}).get("data")


@router.get("/talk/rooms")
async def talk_rooms(authorization: Annotated[str, Header()]):
    """Lista las conversaciones del usuario (bandeja)."""
    data = await _talk("GET", "/api/v4/room", authorization)
    rooms = []
    for r in (data or []):
        lm = r.get("lastMessage")
        rooms.append({
            "token": r.get("token"),
            "name": r.get("displayName"),
            "type": r.get("type"),
            # en 1:1 (type 1), room.name es el userId del otro → avatar directo de Nextcloud
            "peerId": r.get("name") if r.get("type") == 1 else None,
            "unread": r.get("unreadMessages", 0),
            "lastActivity": r.get("lastActivity"),
            "lastMessage": (lm or {}).get("message") if isinstance(lm, dict) else None,
            "isFavorite": bool(r.get("isFavorite")),  # marcada como favorita en Talk
        })
    # favoritas primero, luego por actividad reciente
    rooms.sort(key=lambda x: (x.get("isFavorite"), x.get("lastActivity") or 0), reverse=True)
    return rooms


@router.post("/talk/one-to-one")
async def talk_one_to_one(body: OneToOneIn, authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    """Abre (o reutiliza) la conversación 1:1 con otro usuario del workspace."""
    target = db.query(User).filter(User.id == body.userId).first()
    if not target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    data = await _talk("POST", "/api/v4/room", authorization, data={"roomType": 1, "invite": target.nc_user_id})
    return {"token": (data or {}).get("token"), "name": (data or {}).get("displayName")}


@router.get("/talk/rooms/{token}/messages")
async def talk_messages(token: str, authorization: Annotated[str, Header()], lastKnownMessageId: int = 0):
    """Mensajes de una conversación, en orden cronológico.
    - lastKnownMessageId=0 → historial reciente (lookIntoFuture=0).
    - lastKnownMessageId>0 → solo lo NUEVO desde ese id (lookIntoFuture=1, no bloqueante)."""
    nuevos = lastKnownMessageId > 0
    params = {
        "lookIntoFuture": 1 if nuevos else 0,
        "limit": 50,
        "lastKnownMessageId": lastKnownMessageId,
        "setReadMarker": 1,
    }
    if nuevos:
        params["timeout"] = 0  # sondeo no-bloqueante (el cliente hace polling por intervalo)
    data = await _talk("GET", f"/api/v1/chat/{token}", authorization, params=params)
    msgs = []
    for m in (data or []):
        if m.get("systemMessage"):
            continue
        text = m.get("message")
        params = m.get("messageParameters") or {}
        f = params.get("file") if isinstance(params, dict) else None
        file_info = None
        if isinstance(f, dict) and f.get("name"):
            mimetype = (f.get("mimetype") or "")
            file_info = {
                "id": f.get("id"),
                "name": f.get("name"),
                "mimetype": mimetype,
                "isImage": mimetype.startswith("image/"),
                "link": f.get("link"),
            }
            text = f"📎 {f['name']}"  # fallback textual
        parent = None
        par = m.get("parent")
        if isinstance(par, dict) and par.get("id"):
            ptext = par.get("message")
            pparams = par.get("messageParameters") or {}
            pf = pparams.get("file") if isinstance(pparams, dict) else None
            if isinstance(pf, dict) and pf.get("name"):
                ptext = f"📎 {pf['name']}"
            parent = {
                "id": par.get("id"),
                "message": ptext,
                "actorName": par.get("actorDisplayName"),
            }
        msgs.append({
            "id": m.get("id"),
            "actorId": m.get("actorId"),
            "actorName": m.get("actorDisplayName"),
            "message": text,
            "file": file_info,
            "parent": parent,                                # mensaje al que responde (si aplica)
            "reactions": m.get("reactions") or {},          # {emoji: conteo}
            "reactionsSelf": m.get("reactionsSelf") or [],  # emojis que YO puse
            "timestamp": m.get("timestamp"),
        })
    msgs.sort(key=lambda x: x["id"] or 0)  # id monotónico → orden cronológico
    return msgs


@router.get("/talk/rooms/{token}/read-status")
async def talk_read_status(token: str, authorization: Annotated[str, Header()]):
    """Estado de lectura de la conversación:
    - lastCommonRead: último mensaje leído por TODOS (para marcar mis mensajes como ✓✓).
    - lastRead: último mensaje que YO he leído."""
    data = await _talk("GET", f"/api/v4/room/{token}", authorization)
    d = data or {}
    return {
        "lastCommonRead": d.get("lastCommonReadMessage") or 0,
        "lastRead": d.get("lastReadMessage") or 0,
    }


@router.post("/talk/rooms/{token}/messages")
async def talk_send(token: str, body: MensajeTalkIn, authorization: Annotated[str, Header()]):
    """Envía un mensaje a una conversación (opcionalmente como respuesta a otro)."""
    data = {"message": body.message}
    if body.replyTo:
        data["replyTo"] = body.replyTo
    resp = await _talk("POST", f"/api/v1/chat/{token}", authorization, data=data)
    return {"id": (resp or {}).get("id")}


class EditarMsgIn(BaseModel):
    message: str


@router.put("/talk/rooms/{token}/messages/{message_id}")
async def talk_edit(token: str, message_id: int, body: EditarMsgIn, authorization: Annotated[str, Header()]):
    """Edita un mensaje propio (Talk permite editar dentro de una ventana de tiempo)."""
    texto = (body.message or "").strip()
    if not texto:
        raise HTTPException(status_code=400, detail="Mensaje vacío")
    await _talk("PUT", f"/api/v1/chat/{token}/{message_id}", authorization, data={"message": texto})
    return {"ok": True}


@router.delete("/talk/rooms/{token}/messages/{message_id}")
async def talk_delete(token: str, message_id: int, authorization: Annotated[str, Header()]):
    """Borra un mensaje. Talk lo convierte en 'mensaje eliminado' (systemMessage), que
    nuestra lista ya omite → el mensaje desaparece para todos. Solo el autor (o un
    moderador) puede borrar dentro de la ventana permitida; si no, Talk responde 403/405."""
    await _talk("DELETE", f"/api/v1/chat/{token}/{message_id}", authorization)
    return {"ok": True}


class ReaccionIn(BaseModel):
    reaction: str


@router.post("/talk/rooms/{token}/messages/{message_id}/reaction")
async def talk_react(token: str, message_id: int, body: ReaccionIn, authorization: Annotated[str, Header()]):
    """Agrega una reacción (emoji) a un mensaje de Talk."""
    emoji = (body.reaction or "").strip()
    if not emoji:
        raise HTTPException(status_code=400, detail="Reacción vacía")
    await _talk("POST", f"/api/v1/reaction/{token}/{message_id}", authorization, data={"reaction": emoji})
    return {"ok": True}


@router.delete("/talk/rooms/{token}/messages/{message_id}/reaction")
async def talk_unreact(token: str, message_id: int, reaction: str, authorization: Annotated[str, Header()]):
    """Quita una reacción (emoji) que YO puse en un mensaje de Talk."""
    emoji = (reaction or "").strip()
    if not emoji:
        raise HTTPException(status_code=400, detail="Reacción vacía")
    await _talk("DELETE", f"/api/v1/reaction/{token}/{message_id}", authorization, params={"reaction": emoji})
    return {"ok": True}


def _safe_name(name: Optional[str]) -> str:
    name = (name or "archivo").replace("\\", "/").split("/")[-1]
    name = re.sub(r'[\r\n"]+', "", name).strip() or "archivo"
    return name[:120]


@router.post("/talk/rooms/{token}/attachment")
async def talk_attachment(token: str, authorization: Annotated[str, Header()], file: UploadFile = File(...)):
    """Adjunta un archivo a la conversación: lo sube a Nextcloud (WebDAV) y lo comparte
    a la sala (shareType=10), lo que publica el archivo como mensaje en el chat."""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Archivo vacío")
    nc = await get_nc_user_info(authorization)
    uid = nc["id"]
    fname = _safe_name(file.filename)
    remote_name = f"{utc_now().strftime('%Y%m%d%H%M%S')}-{fname}"
    dav_base = f"{NC_URL}/remote.php/dav/files/{urllib.parse.quote(uid)}"
    put_url = f"{dav_base}/Talk/{urllib.parse.quote(remote_name)}"
    async with httpx.AsyncClient(timeout=90.0) as client:
        # asegurar la carpeta Talk (ignora si ya existe)
        try:
            await client.request("MKCOL", f"{dav_base}/Talk/", headers={"Authorization": authorization})
        except Exception:
            pass
        put = await client.put(
            put_url,
            headers={"Authorization": authorization, "Content-Type": file.content_type or "application/octet-stream"},
            content=content,
        )
        if put.status_code not in (200, 201, 204):
            raise HTTPException(status_code=502, detail=f"No se pudo subir el archivo (dav {put.status_code})")
        share = await client.post(
            f"{NC_URL}/ocs/v2.php/apps/files_sharing/api/v1/shares",
            headers={"Authorization": authorization, "OCS-APIRequest": "true", "Accept": "application/json"},
            # OJO: el path va SIN slash inicial ("Talk/x", no "/Talk/x") o da 404.
            data={"path": f"Talk/{remote_name}", "shareType": 10, "shareWith": token},
        )
        if share.status_code not in (200, 201):
            raise HTTPException(status_code=502, detail=f"No se pudo compartir el archivo ({share.status_code})")
    return {"ok": True, "file": fname}


@router.get("/talk/file")
async def talk_file(fileId: int, authorization: Annotated[str, Header()], x: int = 1024, y: int = 1024):
    """Preview de un archivo (imagen) compartido en Talk. Lo trae de Nextcloud con el
    token del usuario y lo devuelve como bytes, para mostrarlo inline en el chat."""
    url = f"{NC_URL}/index.php/core/preview?fileId={fileId}&x={x}&y={y}&a=1&forceIcon=0"
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"Authorization": authorization})
    except Exception:
        raise HTTPException(status_code=502, detail="No se pudo obtener la imagen")
    if resp.status_code != 200 or not resp.content:
        raise HTTPException(status_code=404, detail="Sin preview")
    return Response(
        content=resp.content,
        media_type=resp.headers.get("content-type", "image/jpeg"),
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get("/talk/rooms/{token}/avatar")
async def talk_avatar(token: str, authorization: Annotated[str, Header()]):
    """Imagen de la conversación (foto del usuario en 1:1, del grupo, o generada)."""
    headers = {"Authorization": authorization, "OCS-APIRequest": "true"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(f"{_TALK}/api/v1/room/{token}/avatar", headers=headers)
    except Exception:
        raise HTTPException(status_code=502, detail="No se pudo obtener el avatar")
    if resp.status_code != 200 or not resp.content:
        raise HTTPException(status_code=404, detail="Sin avatar")
    return Response(
        content=resp.content,
        media_type=resp.headers.get("content-type", "image/png"),
        headers={"Cache-Control": "private, max-age=3600"},
    )


# ============================================================
# PRESENCIA DE NEXTCLOUD — quién está conectado en NC (Talk/Calendar/etc.)
#  Sirve para mostrar su "presencia fantasma" en el mundo del workspace.
# ============================================================

@router.get("/nextcloud-online")
async def nextcloud_online(authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    """Usuarios del workspace conectados en Nextcloud (según la User Status API).
    status ∈ {online, away, dnd} se considera 'presente'. Incluye departamento y
    avatar para pintar su muñequito fantasma."""
    await _resolve_user(authorization, db)  # requiere sesión válida
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                f"{NC_URL}/ocs/v2.php/apps/user_status/api/v1/statuses",
                headers={"Authorization": authorization, "OCS-APIRequest": "true", "Accept": "application/json"},
                params={"limit": 200},
            )
    except Exception:
        return []
    if resp.status_code != 200:
        return []
    try:
        data = (resp.json().get("ocs") or {}).get("data") or []
    except Exception:
        return []
    estado = {}
    for s in data:
        uid = s.get("userId")
        if uid:
            estado[uid] = (s.get("status") or "").lower()
    presentes = {"online", "away", "dnd"}
    users = db.query(User).filter(User.is_active.is_(True)).all()
    profs = {p.user_id: p for p in db.query(WorkspaceProfile).all()}
    out = []
    for u in users:
        st = estado.get(u.nc_user_id)
        if st in presentes:
            prof = profs.get(u.id)
            out.append({
                "id": u.id,
                "ncid": u.nc_user_id,
                "name": u.display_name,
                "role": u.job_title,
                "status": st,
                "team": (u.team.name if u.team else None),        # equipo del Deck (fuente fiable)
                "departamento": (prof.departamento if prof else None),  # onboarding workspace (opcional)
                "avatar": (prof.avatar if prof else None),
            })
    return out


# ============================================================
# NEWS — tablero de novedades del Holding
#  Notas de colaboradores, avisos empresariales, cumpleaños, etc.
# ============================================================

class NewsIn(BaseModel):
    cuerpo: str = ""
    titulo: Optional[str] = None
    tipo: Optional[str] = "nota"  # nota | empresa | cumple | evento
    imagen_url: Optional[str] = None
    scope: Optional[str] = "general"  # "general" (Holding) | "oficina" (mi oficina)


_NEWS_TIPOS = {"nota", "empresa", "cumple", "evento"}


def _news_dict(n: WorkspaceNews) -> dict:
    a = n.autor
    return {
        "id": n.id,
        "tipo": n.tipo,
        "titulo": n.titulo,
        "cuerpo": n.cuerpo,
        "imagen_url": n.imagen_url,
        "oficina": n.oficina,
        "fijado": bool(n.fijado),
        "created_at": to_rfc3339_z(n.created_at),
        "autor_id": n.autor_id,
        "autor_nombre": a.display_name if a else None,
        "autor_ncid": a.nc_user_id if a else None,
    }


@router.get("/news")
async def listar_news(
    authorization: Annotated[str, Header()],
    limit: int = 30,
    scope: str = "general",   # "general" (Holding) | "oficina"
    oficina: Optional[str] = None,  # id de oficina explícito (tablero de esa oficina); si no, la del usuario
    db: Session = Depends(get_db),
):
    """Novedades: primero fijadas, luego por fecha desc. scope filtra general vs oficina.
    Con scope=oficina se puede pedir una oficina concreta (los tableros de cada oficina),
    o la del usuario si no se especifica."""
    user = await _resolve_user(authorization, db)
    limit = max(1, min(limit, 100))
    q = db.query(WorkspaceNews)
    if scope == "oficina":
        ofi_id = oficina if (oficina and oficina in _OFICINA_IDS) else None
        if not ofi_id:
            ofi = _oficina_de(user)
            if not ofi:
                return []
            ofi_id = ofi[0]
        q = q.filter(WorkspaceNews.oficina == ofi_id)
    else:
        q = q.filter(WorkspaceNews.oficina.is_(None))
    rows = q.order_by(WorkspaceNews.fijado.desc(), WorkspaceNews.created_at.desc()).limit(limit).all()
    return [_news_dict(n) for n in rows]


@router.post("/news")
async def crear_news(
    body: NewsIn,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    """Publica una novedad (nota de colaborador). El autor es el usuario del token."""
    user = await _resolve_user(authorization, db)
    cuerpo = (body.cuerpo or "").strip()
    imagen_url = (body.imagen_url or "").strip() or None
    if not cuerpo and not imagen_url:
        raise HTTPException(status_code=400, detail="La novedad no puede estar vacía")
    # solo aceptamos URLs de imagen de nuestro propio Nextcloud (evita inyectar enlaces externos)
    if imagen_url and not imagen_url.startswith(NC_URL):
        imagen_url = None
    tipo = (body.tipo or "nota").strip().lower()
    if tipo not in _NEWS_TIPOS:
        tipo = "nota"
    # Solo un gerente/admin puede publicar avisos "empresa" (comunicados oficiales).
    if tipo == "empresa" and not _is_manager(user):
        tipo = "nota"
    # ámbito: general (Holding) o la oficina del usuario
    oficina = None
    if (body.scope or "general") == "oficina":
        ofi = _oficina_de(user)
        if not ofi:
            raise HTTPException(status_code=400, detail="No tienes una oficina asignada")
        oficina = ofi[0]
    n = WorkspaceNews(
        tipo=tipo,
        titulo=(body.titulo or "").strip() or None,
        cuerpo=cuerpo[:4000],
        imagen_url=imagen_url,
        oficina=oficina,
        autor_id=user.id,
        created_at=utc_now(),
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    return _news_dict(n)


@router.post("/news/upload")
async def news_upload(authorization: Annotated[str, Header()], file: UploadFile = File(...)):
    """Sube una imagen para una novedad: la guarda en Nextcloud (carpeta Workspace-News)
    con enlace público y devuelve la URL de descarga directa (para usar en <img>)."""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Archivo vacío")
    if len(content) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Imagen muy grande (máx 8 MB)")
    ctype = file.content_type or ""
    if not ctype.startswith("image/"):
        raise HTTPException(status_code=400, detail="Solo se permiten imágenes")
    nc = await get_nc_user_info(authorization)
    uid = nc["id"]
    fname = _safe_name(file.filename or "imagen.png")
    remote_name = f"{utc_now().strftime('%Y%m%d%H%M%S')}-{fname}"
    folder = "Workspace-News"
    dav_base = f"{NC_URL}/remote.php/dav/files/{urllib.parse.quote(uid)}"
    put_url = f"{dav_base}/{folder}/{urllib.parse.quote(remote_name)}"
    async with httpx.AsyncClient(timeout=90.0) as client:
        try:
            await client.request("MKCOL", f"{dav_base}/{folder}/", headers={"Authorization": authorization})
        except Exception:
            pass
        put = await client.put(
            put_url,
            headers={"Authorization": authorization, "Content-Type": ctype},
            content=content,
        )
        if put.status_code not in (200, 201, 204):
            raise HTTPException(status_code=502, detail=f"No se pudo subir la imagen (dav {put.status_code})")
        # enlace público (shareType=3), solo lectura (permissions=1)
        share = await client.post(
            f"{NC_URL}/ocs/v2.php/apps/files_sharing/api/v1/shares",
            headers={"Authorization": authorization, "OCS-APIRequest": "true", "Accept": "application/json"},
            data={"path": f"{folder}/{remote_name}", "shareType": 3, "permissions": 1},
        )
        if share.status_code not in (200, 201):
            raise HTTPException(status_code=502, detail=f"No se pudo compartir la imagen ({share.status_code})")
        token = (((share.json().get("ocs") or {}).get("data") or {}).get("token"))
        if not token:
            raise HTTPException(status_code=502, detail="Enlace de imagen sin token")
    return {"url": f"{NC_URL}/s/{token}/download"}


@router.delete("/news/{news_id}")
async def borrar_news(
    news_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    """Borra una novedad. Solo el autor o un gerente/admin."""
    user = await _resolve_user(authorization, db)
    n = db.query(WorkspaceNews).filter(WorkspaceNews.id == news_id).first()
    if not n:
        raise HTTPException(status_code=404, detail="Novedad no encontrada")
    if n.autor_id != user.id and not _is_manager(user):
        raise HTTPException(status_code=403, detail="No puedes borrar esta novedad")
    db.delete(n)
    db.commit()
    return {"ok": True}


# ============================================================
# WEB PUSH (PWA) — notificaciones nativas al celular
# ============================================================
class PushSubIn(BaseModel):
    endpoint: str
    keys: dict = {}


@router.get("/push/key")
async def push_key():
    """Clave pública VAPID para que el navegador se suscriba. Vacía = push apagado."""
    return {"publicKey": _cfg.VAPID_PUBLIC_KEY if push_habilitado() else ""}


@router.post("/push/subscribe")
async def push_subscribe(
    body: PushSubIn,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    """Guarda (o actualiza) la suscripción Web Push del navegador del usuario."""
    user = await _resolve_user(authorization, db)
    endpoint = (body.endpoint or "").strip()
    p256dh = (body.keys or {}).get("p256dh")
    auth = (body.keys or {}).get("auth")
    if not endpoint or not p256dh or not auth:
        raise HTTPException(status_code=400, detail="Suscripción incompleta")
    sub = db.query(WorkspacePushSubscription).filter(
        WorkspacePushSubscription.endpoint == endpoint).first()
    if sub:
        sub.user_id = user.id
        sub.p256dh = p256dh
        sub.auth = auth
    else:
        db.add(WorkspacePushSubscription(
            user_id=user.id, endpoint=endpoint, p256dh=p256dh, auth=auth))
    db.commit()
    return {"ok": True}


@router.delete("/push/subscribe")
async def push_unsubscribe(
    body: PushSubIn,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    """Elimina una suscripción (cuando el navegador la revoca)."""
    await _resolve_user(authorization, db)
    endpoint = (body.endpoint or "").strip()
    if endpoint:
        db.query(WorkspacePushSubscription).filter(
            WorkspacePushSubscription.endpoint == endpoint).delete()
        db.commit()
    return {"ok": True}


@router.post("/push/test")
async def push_test(
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    """Envía un push de prueba al propio usuario (para verificar la config)."""
    user = await _resolve_user(authorization, db)
    n = enviar_push(db, user.id, "GCF Workspace",
                    "🔔 Notificaciones activadas correctamente.", url="/", tag="gcf-test")
    return {"enviados": n, "habilitado": push_habilitado()}


class PushNotifyIn(BaseModel):
    userId: int
    title: str
    body: str = ""
    url: str = "/"
    tag: str = "gcf-workspace"


@router.post("/push/notify")
async def push_notify(
    body: PushNotifyIn,
    db: Session = Depends(get_db),
    x_push_secret: Annotated[str | None, Header()] = None,
):
    """Puente servidor-a-servidor: el Node del workspace pide un push para un
    usuario (llamada/nudge/reunión cuando NO está conectado). Protegido por secreto."""
    if not _cfg.PUSH_BRIDGE_SECRET or x_push_secret != _cfg.PUSH_BRIDGE_SECRET:
        raise HTTPException(status_code=403, detail="No autorizado")
    n = enviar_push(db, body.userId, body.title, body.body, url=body.url, tag=body.tag)
    return {"enviados": n}


@router.post("/push/nc-token")
async def push_nc_token(
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    """Cachea (cifrado) el access token de Nextcloud del usuario para que el poller
    de Talk pueda consultarle mensajes con la app cerrada. El cliente lo llama al
    activar el push y cada pocos minutos mientras está abierto (para mantenerlo
    fresco). El token sale del propio header Authorization."""
    user = await _resolve_user(authorization, db)
    if not cifrado_disponible():
        return {"ok": False, "motivo": "cifrado no configurado"}
    token = (authorization or "").split(" ")[-1].strip()
    if token:
        guardar_access_token(db, user.id, token, expires_in=3600)
    return {"ok": True}
