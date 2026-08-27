"""
Asistente por voz del Workspace (Fase 2B) — notas, recordatorios, auditoría
e historial de conversación.

Montado en /api/workspace/assistant. Mismo patrón que workspace.py: la identidad
SIEMPRE sale del token Nextcloud vía _resolve_user, nunca del body.

Notas:
- Ningún endpoint acepta un usuario_id del cliente; el filtrado por usuario va en
  el WHERE, igual que en /reuniones/hoy. Aquí quien escribe es un modelo de
  lenguaje interpretando voz.
- Todo se persiste en UTC: `ensure_aware_utc` rechaza los datetime naive.
- La zona horaria de un recordatorio es un METADATO: se guarda para poder
  re-renderizar y auditar, y NO reinterpreta `vence_en`. El instante sigue
  saliendo del offset que trae el ISO, como hasta ahora.
"""
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import Annotated, List, Optional, Literal
from datetime import datetime
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field

from app.api.dependencies import get_db
from app.core.datetime_utils import utc_now, to_rfc3339_z, ensure_aware_utc
from app.db.models import (
    User, WorkspaceAssistantNote, WorkspaceAssistantReminder, WorkspaceAssistantLog,
    WorkspaceAssistantThread, WorkspaceAssistantMessage,
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
    # Zona IANA de quien lo crea. Opcional: si no viene se persiste el default
    # de la columna. NO altera `vence_en` — el instante sale del offset del ISO.
    zona_horaria: Optional[str] = None


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
    zona_horaria: Optional[str] = None    # metadato; no reinterpreta vence_en


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


def _zona_o_422(z: Optional[str]) -> Optional[str]:
    """Zona IANA validada, o 422. Devuelve None cuando no la mandan, y entonces
    manda el default de la columna.

    Se valida en la puerta por el mismo motivo que el offset ausente: una zona
    inventada guardada en silencio no da la cara hasta que alguien intenta
    re-renderizar con ella, y para entonces ya no se sabe cuál era la buena.
    ZoneInfo lanza ZoneInfoNotFoundError (que es un KeyError) si la clave no
    existe, y ValueError si ni siquiera tiene forma de clave.
    """
    if z is None:
        return None
    z = z.strip()
    if not z:
        return None
    try:
        ZoneInfo(z)
    except (KeyError, ValueError):
        raise HTTPException(status_code=422, detail=f"Zona horaria desconocida: {z!r}")
    return z


def _recordatorio_dict(r: WorkspaceAssistantReminder) -> dict:
    return {
        "id": r.id,
        "texto": r.texto,
        "vence_en": to_rfc3339_z(r.vence_en),
        "zona_horaria": r.zona_horaria,
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
    zona = _zona_o_422(body.zona_horaria)
    r = WorkspaceAssistantReminder(
        usuario_id=user.id,
        texto=body.texto.strip(),
        vence_en=vence,
        estado="pendiente",
        created_at=utc_now(),
    )
    # Sin zona no se fuerza nada: la columna trae su propio default, y así una
    # fila creada por un cliente que no la manda queda igual que las de antes.
    if zona:
        r.zona_horaria = zona
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
    if (
        body.estado is None and body.texto is None
        and body.vence_en is None and body.zona_horaria is None
    ):
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

    # Se reprograma desde otro huso: la hora nueva ya viene resuelta en `vence_en`,
    # y esto solo deja constancia de con qué reloj se pidió. Va aparte de
    # `vence_en` porque cambiar de zona sin mover la hora es un caso real —
    # corregir el metadato de un recordatorio que se creó con la zona equivocada.
    zona_nueva = _zona_o_422(body.zona_horaria)
    if zona_nueva:
        r.zona_horaria = zona_nueva

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


# ============================================================
# HISTORIAL DE CONVERSACIÓN
# ============================================================
#
# Un hilo agrupa los turnos de una charla; cada turno es una fila con su hora.
# Tres decisiones que conviene no deshacer sin leer antes:
#
#  · La hora la estampa el SERVIDOR. Es lo que se pinta debajo de cada burbuja,
#    y un reloj de navegador adelantado dejaría el historial en un orden que no
#    ocurrió. El cliente convierte a su huso al pintar, nunca al guardar.
#  · Los hilos vacíos no existen. El hilo se crea con el primer mensaje ya
#    escrito, así que no hay forma de acumular hilos en blanco a base de pulsar
#    "conversación nueva".
#  · Borrar TODO exige `confirmar=si`. Un DELETE sobre la colección es la única
#    ruta de este archivo que destruye datos de varios hilos a la vez.

LARGO_TITULO = 120
TOPE_HILOS = 100
TOPE_MENSAJES = 200


class HiloIn(BaseModel):
    titulo: str = Field(min_length=1, max_length=LARGO_TITULO)


class MensajeIn(BaseModel):
    rol: Literal["usuario", "asistente"]
    contenido: str = Field(min_length=1)
    origen: Literal["voz", "texto"] = "texto"


def _hilo_dict(h: WorkspaceAssistantThread, mensajes: Optional[int] = None) -> dict:
    d = {
        "id": h.id,
        "titulo": h.titulo,
        "created_at": to_rfc3339_z(h.created_at),
        "updated_at": to_rfc3339_z(h.updated_at),
    }
    if mensajes is not None:
        d["mensajes"] = mensajes
    return d


def _mensaje_dict(m: WorkspaceAssistantMessage) -> dict:
    return {
        "id": m.id,
        "rol": m.rol,
        "contenido": m.contenido,
        "origen": m.origen,
        "created_at": to_rfc3339_z(m.created_at),
    }


def _hilo_o_404(hilo_id: int, user: User, db: Session) -> WorkspaceAssistantThread:
    """El hilo de ESA persona, o 404. `usuario_id` va en el WHERE por el mismo
    motivo que en las notas: un hilo ajeno no existe para esta query, así que
    responde 404 y no 403. Un 403 confirmaría que ese id existe y convertiría la
    ruta en un sondeo de conversaciones de otras personas."""
    h = (
        db.query(WorkspaceAssistantThread)
        .filter(
            WorkspaceAssistantThread.id == hilo_id,
            WorkspaceAssistantThread.usuario_id == user.id,
        )
        .first()
    )
    if not h:
        raise HTTPException(status_code=404, detail="Hilo no encontrado")
    return h


def _titulo_limpio(texto: str) -> str:
    """Una sola línea, sin espacios de más y recortada al ancho de la columna.
    Se recorta con puntos suspensivos para que se note que hay más texto: un
    corte seco se lee como si la frase hubiera terminado ahí."""
    plano = " ".join(texto.split())
    if len(plano) <= LARGO_TITULO:
        return plano
    return plano[: LARGO_TITULO - 1].rstrip() + "…"


@router.post("/hilos")
async def crear_hilo(body: HiloIn, authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    """Abre un hilo. El título lo manda el cliente con la primera frase de la
    persona ya escrita — no lo redacta el modelo, que al reintentar devolvería
    otro y el hilo dejaría de reconocerse en la lista."""
    user = await _resolve_user(authorization, db)
    titulo = _titulo_limpio(body.titulo)
    if not titulo:
        raise HTTPException(status_code=422, detail="El título no puede quedar vacío")
    h = WorkspaceAssistantThread(
        usuario_id=user.id,          # del TOKEN, no del body
        titulo=titulo,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(h)
    db.commit()
    db.refresh(h)
    return _hilo_dict(h, mensajes=0)


@router.get("/hilos")
async def listar_hilos(
    authorization: Annotated[str, Header()],
    limit: int = 30,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """Los hilos de la persona, del más reciente al más antiguo. El contador de
    mensajes sale de un GROUP BY en la misma consulta: pedirlo hilo por hilo
    sería un N+1 que crece con cada conversación guardada."""
    user = await _resolve_user(authorization, db)
    n = min(max(limit, 1), TOPE_HILOS)
    desde = max(offset, 0)
    filas = (
        db.query(
            WorkspaceAssistantThread,
            func.count(WorkspaceAssistantMessage.id).label("mensajes"),
        )
        .outerjoin(
            WorkspaceAssistantMessage,
            WorkspaceAssistantMessage.hilo_id == WorkspaceAssistantThread.id,
        )
        .filter(WorkspaceAssistantThread.usuario_id == user.id)   # filtro en el SERVIDOR
        .group_by(WorkspaceAssistantThread.id)
        .order_by(WorkspaceAssistantThread.updated_at.desc())
        .limit(n)
        .offset(desde)
        .all()
    )
    return [_hilo_dict(h, mensajes=int(c or 0)) for h, c in filas]


@router.get("/hilos/{hilo_id}/mensajes")
async def listar_mensajes(
    hilo_id: int,
    authorization: Annotated[str, Header()],
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """Los turnos de un hilo, en orden de llegada. Se pagina como todo lo demás:
    una conversación larga no puede devolverse entera por costumbre."""
    user = await _resolve_user(authorization, db)
    h = _hilo_o_404(hilo_id, user, db)
    n = min(max(limit, 1), TOPE_MENSAJES)
    desde = max(offset, 0)
    filas = (
        db.query(WorkspaceAssistantMessage)
        .filter(WorkspaceAssistantMessage.hilo_id == h.id)
        .order_by(WorkspaceAssistantMessage.id.asc())
        .limit(n)
        .offset(desde)
        .all()
    )
    return {"hilo": _hilo_dict(h), "mensajes": [_mensaje_dict(m) for m in filas]}


@router.post("/hilos/{hilo_id}/mensajes")
async def agregar_mensaje(
    hilo_id: int,
    body: MensajeIn,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    """Añade un turno y adelanta el `updated_at` del hilo, que es lo que ordena
    la lista. Las dos escrituras van en la MISMA transacción: un mensaje
    guardado en un hilo que sigue figurando como viejo se hunde en la lista y
    la persona no lo encuentra."""
    user = await _resolve_user(authorization, db)
    h = _hilo_o_404(hilo_id, user, db)
    ahora = utc_now()
    m = WorkspaceAssistantMessage(
        hilo_id=h.id,
        rol=body.rol,
        contenido=body.contenido,
        origen=body.origen,
        created_at=ahora,
    )
    db.add(m)
    h.updated_at = ahora
    db.commit()
    db.refresh(m)
    return _mensaje_dict(m)


@router.delete("/hilos/{hilo_id}")
async def borrar_hilo(hilo_id: int, authorization: Annotated[str, Header()], db: Session = Depends(get_db)):
    """Borra un hilo y sus mensajes. El borrado de los mensajes lo hace el
    ON DELETE CASCADE de la FK, no un bucle en Python."""
    user = await _resolve_user(authorization, db)
    h = _hilo_o_404(hilo_id, user, db)
    db.delete(h)
    db.commit()
    return {"ok": True}


@router.delete("/hilos")
async def borrar_todos_los_hilos(
    authorization: Annotated[str, Header()],
    confirmar: str = "",
    db: Session = Depends(get_db),
):
    """Borra TODAS las conversaciones de la persona. Exige `?confirmar=si` a
    propósito: es la única ruta de este archivo que destruye varios hilos de una
    vez, y un DELETE sobre la colección se dispara demasiado fácil —desde una
    ruta mal formada o un reintento— para no pedir nada."""
    user = await _resolve_user(authorization, db)
    if confirmar != "si":
        raise HTTPException(status_code=422, detail="Falta confirmar=si")
    borrados = (
        db.query(WorkspaceAssistantThread)
        .filter(WorkspaceAssistantThread.usuario_id == user.id)
        .delete(synchronize_session=False)
    )
    db.commit()
    return {"ok": True, "borrados": int(borrados or 0)}
