"""
Asistente por voz del Workspace (Fase 2B) — notas, recordatorios y auditoría.

Montado en /api/workspace/assistant. Mismo patrón que workspace.py: la identidad
SIEMPRE sale del token Nextcloud vía _resolve_user, nunca del body.

Notas:
- Ningún endpoint acepta un usuario_id del cliente; el filtrado por usuario va en
  el WHERE, igual que en /reuniones/hoy. Aquí quien escribe es un modelo de
  lenguaje interpretando voz.
- Todo se persiste en UTC: `ensure_aware_utc` rechaza los datetime naive.
"""
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from typing import Annotated, List, Optional, Literal
from datetime import datetime
from pydantic import BaseModel, Field

from app.api.dependencies import get_db
from app.core.datetime_utils import utc_now, to_rfc3339_z, ensure_aware_utc
from app.db.models import (
    User, WorkspaceAssistantNote, WorkspaceAssistantReminder, WorkspaceAssistantLog,
)
from app.api.v1.workspace import _resolve_user

router = APIRouter()


# ============================================================
# SCHEMAS
# ============================================================

class NotaIn(BaseModel):
    titulo: str = Field(min_length=1, max_length=255)
    cuerpo: str = Field(min_length=1)
    origen: Literal["voz", "texto"] = "voz"


class RecordatorioIn(BaseModel):
    texto: str = Field(min_length=1, max_length=500)
    vence_en: datetime          # ISO con offset; se normaliza a UTC


class NotaPatch(BaseModel):
    """Editar una nota. Los dos campos son opcionales, pero mandar los dos en
    None se rechaza: un PATCH que no cambia nada no debe responder 200."""
    titulo: Optional[str] = Field(default=None, min_length=1, max_length=255)
    cuerpo: Optional[str] = Field(default=None, min_length=1)


class RecordatorioPatch(BaseModel):
    # Dos operaciones distintas por la misma puerta: cancelar y reprogramar.
    # 'notificado' sigue siendo solo del scheduler; el cliente no puede ponerlo.
    estado: Optional[Literal["cancelado"]] = None
    texto: Optional[str] = Field(default=None, min_length=1, max_length=500)
    vence_en: Optional[datetime] = None   # ISO con offset; se normaliza a UTC


class LogIn(BaseModel):
    accion: str = Field(min_length=1, max_length=60)
    argumentos: Optional[dict] = None
    resultado: Literal["ok", "error"]
    detalle: Optional[str] = None
    transcripcion: Optional[str] = None


# ============================================================
# HELPERS
# ============================================================

def _nota_dict(n: WorkspaceAssistantNote) -> dict:
    return {
        "id": n.id,
        "titulo": n.titulo,
        "cuerpo": n.cuerpo,
        "origen": n.origen,
        "created_at": to_rfc3339_z(n.created_at),
        "updated_at": to_rfc3339_z(n.updated_at),
    }


def _recordatorio_dict(r: WorkspaceAssistantReminder) -> dict:
    return {
        "id": r.id,
        "texto": r.texto,
        "vence_en": to_rfc3339_z(r.vence_en),
        "estado": r.estado,
        "notificado_en": to_rfc3339_z(r.notificado_en),
        "created_at": to_rfc3339_z(r.created_at),
    }


# ============================================================
# NOTAS
# ============================================================

@router.post("/notas")
async def crear_nota(body: NotaIn, authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    user = await _resolve_user(authorization, db)
    n = WorkspaceAssistantNote(
        usuario_id=user.id,          # del TOKEN, no del body
        titulo=body.titulo.strip(),
        cuerpo=body.cuerpo.strip(),
        origen=body.origen,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    return _nota_dict(n)


@router.get("/notas")
async def listar_notas(authorization: Annotated[str, Header()], limit: int = 20, db: Session = Depends(get_db)):
    user = await _resolve_user(authorization, db)
    n = min(max(limit, 1), 100)
    rows = (
        db.query(WorkspaceAssistantNote)
        .filter(WorkspaceAssistantNote.usuario_id == user.id)   # filtro en el SERVIDOR
        .order_by(WorkspaceAssistantNote.created_at.desc())
        .limit(n)
        .all()
    )
    return [_nota_dict(x) for x in rows]


@router.patch("/notas/{nota_id}")
async def editar_nota(
    nota_id: int,
    body: NotaPatch,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    """Edita título y/o cuerpo. `usuario_id` va en el WHERE igual que en el
    DELETE: una nota ajena no existe para esta query, así que responde 404 y no
    403. La diferencia importa — un 403 confirmaría que ese id existe y
    convertiría la ruta en un sondeo de notas de otras personas."""
    user = await _resolve_user(authorization, db)
    if body.titulo is None and body.cuerpo is None:
        raise HTTPException(status_code=422, detail="No hay nada que cambiar")
    n = (
        db.query(WorkspaceAssistantNote)
        .filter(
            WorkspaceAssistantNote.id == nota_id,
            WorkspaceAssistantNote.usuario_id == user.id,
        )
        .first()
    )
    if not n:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if body.titulo is not None:
        n.titulo = body.titulo.strip()
    if body.cuerpo is not None:
        n.cuerpo = body.cuerpo.strip()
    # `origen` NO se toca: dice de dónde salió la nota, no quién la editó. Una
    # nota dictada sigue arrastrando los errores de su transcripción original.
    n.updated_at = utc_now()
    db.commit()
    db.refresh(n)
    return _nota_dict(n)


@router.delete("/notas/{nota_id}")
async def borrar_nota(nota_id: int, authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    user = await _resolve_user(authorization, db)
    # usuario_id en el WHERE: una nota ajena simplemente no existe para esta query.
    n = (
        db.query(WorkspaceAssistantNote)
        .filter(
            WorkspaceAssistantNote.id == nota_id,
            WorkspaceAssistantNote.usuario_id == user.id,
        )
        .first()
    )
    if not n:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    db.delete(n)
    db.commit()
    return {"ok": True}


# ============================================================
# RECORDATORIOS
# ============================================================

@router.post("/recordatorios")
async def crear_recordatorio(body: RecordatorioIn, authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    user = await _resolve_user(authorization, db)
    try:
        vence = ensure_aware_utc(body.vence_en)
    except ValueError as e:
        # Sin offset el recordatorio sonaría con la zona del servidor: no se silencia.
        raise HTTPException(status_code=422, detail=str(e))
    r = WorkspaceAssistantReminder(
        usuario_id=user.id,
        texto=body.texto.strip(),
        vence_en=vence,
        estado="pendiente",
        created_at=utc_now(),
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return _recordatorio_dict(r)


@router.get("/recordatorios")
async def listar_recordatorios(
    authorization: Annotated[str, Header()],
    estado: Optional[Literal["pendiente", "notificado", "cancelado"]] = None,
    db: Session = Depends(get_db),
):
    user = await _resolve_user(authorization, db)
    q = db.query(WorkspaceAssistantReminder).filter(WorkspaceAssistantReminder.usuario_id == user.id)
    if estado:
        q = q.filter(WorkspaceAssistantReminder.estado == estado)
    rows = q.order_by(WorkspaceAssistantReminder.vence_en.asc()).limit(100).all()
    return [_recordatorio_dict(x) for x in rows]


@router.patch("/recordatorios/{recordatorio_id}")
async def editar_recordatorio(
    recordatorio_id: int,
    body: RecordatorioPatch,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    """Cancelar o reprogramar. Van juntos porque son el mismo recurso y el mismo
    dueño; lo que cambia es qué campos llegan."""
    user = await _resolve_user(authorization, db)
    if body.estado is None and body.texto is None and body.vence_en is None:
        raise HTTPException(status_code=422, detail="No hay nada que cambiar")
    r = (
        db.query(WorkspaceAssistantReminder)
        .filter(
            WorkspaceAssistantReminder.id == recordatorio_id,
            WorkspaceAssistantReminder.usuario_id == user.id,   # 404, no 403: ver editar_nota
        )
        .first()
    )
    if not r:
        raise HTTPException(status_code=404, detail="Recordatorio no encontrado")

    # Cancelar uno ya notificado ocultaría un aviso que la persona ya recibió.
    # Reprogramarlo NO: mover al futuro uno que ya sonó es justo el caso de
    # "pospónmelo", así que esa comprobación se queda solo en la cancelación.
    if body.estado == "cancelado":
        if r.estado == "notificado":
            raise HTTPException(status_code=409, detail="Ese recordatorio ya se notificó")
        r.estado = "cancelado"

    if body.texto is not None:
        r.texto = body.texto.strip()

    if body.vence_en is not None:
        try:
            r.vence_en = ensure_aware_utc(body.vence_en)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        # Al reprogramar vuelve a estar pendiente y se borra la marca de aviso:
        # un recordatorio ya notificado que se mueve al futuro tiene que volver a
        # sonar, y con `notificado_en` puesto el scheduler lo dejaría pasar.
        # Va después del bloque de cancelación a propósito: "cancélalo y muévelo"
        # no tiene sentido, y si llegan los dos manda la fecha nueva.
        r.estado = "pendiente"
        r.notificado_en = None

    db.commit()
    db.refresh(r)
    return _recordatorio_dict(r)


# ============================================================
# AUDITORÍA
# ============================================================

@router.post("/log")
async def registrar_log(body: LogIn, authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    """Registra una entrada de auditoría. Solo escritura: no hay GET expuesto a la
    app, para que ni el agente ni el usuario toquen el rastro. Se consulta en la BD."""
    user = await _resolve_user(authorization, db)
    entrada = WorkspaceAssistantLog(
        usuario_id=user.id,
        accion=body.accion[:60],
        argumentos=body.argumentos,
        resultado=body.resultado,
        detalle=body.detalle,
        transcripcion=body.transcripcion,
        created_at=utc_now(),
    )
    db.add(entrada)
    db.commit()
    db.refresh(entrada)
    return {"id": entrada.id}
