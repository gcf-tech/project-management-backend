"""Poller de push nativo para mensajes de Nextcloud Talk.

Con la app CERRADA nadie está mirando Talk, así que este bucle consulta las
conversaciones de cada usuario (con su access token cacheado y cifrado) y le
manda push si hay mensajes nuevos. Detalles:

- Solo procesa usuarios cuyo token siga vivo, que tengan suscripción de push y
  cuya app parezca CERRADA (no refrescó el token hace >150s → heurística de
  actividad; si está activo, ya ve los mensajes en la bandeja in-app).
- `talk_seen` (JSON {roomToken: lastMessageId}) evita repetir avisos. La primera
  vez solo fija la línea base (no notifica) para no soltar un aluvión inicial.
"""
import asyncio
import json
import traceback
from datetime import timedelta

from app.db.database import SessionLocal
from app.db.models import WorkspaceUserToken, WorkspacePushSubscription
from app.services.push import enviar_push, push_habilitado
from app.services.push_tokens import leer_access_token, cifrado_disponible
from app.core.datetime_utils import utc_now

INTERVALO_S = 90            # cada 90 s
INACTIVO_S = 150           # el token no se refresca hace >150s → app cerrada


async def _procesar_usuario(db, _talk, row) -> None:
    token = leer_access_token(row)
    if not token:
        return
    auth = "Bearer " + token
    try:
        data = await _talk("GET", "/api/v4/room", auth)
    except Exception:
        return  # token expirado/ inválido u otro fallo → se ignora (caduca solo)

    seen = {}
    if row.talk_seen:
        try:
            seen = json.loads(row.talk_seen)
        except Exception:
            seen = {}
    primera_vez = not row.talk_seen

    cambiado = False
    nuevos = 0
    top = None  # conversación con el mensaje sin leer más reciente
    for r in (data or []):
        tk = r.get("token")
        lm = r.get("lastMessage")
        if not tk or not isinstance(lm, dict):
            continue
        mid = lm.get("id") or 0
        prev = seen.get(tk, 0)
        unread = r.get("unreadMessages", 0)
        # mensaje nuevo, sin leer, no de sistema (unread>0 ya implica que es de otros)
        if not primera_vez and unread > 0 and mid > prev and not lm.get("systemMessage"):
            nuevos += unread
            es_grupo = r.get("type") != 1  # type 1 = 1:1
            autor = lm.get("actorDisplayName") or "Alguien"
            # en grupo mostramos "Sala: Autor"; en 1:1 solo el autor
            titulo_conv = f"{r.get('displayName')}" if es_grupo and r.get("displayName") else autor
            if top is None or mid > top["mid"]:
                top = {"mid": mid, "token": tk, "titulo": titulo_conv,
                       "autor": autor, "texto": lm.get("message") or "", "grupo": es_grupo}
        if mid and mid != prev:
            seen[tk] = mid
            cambiado = True

    if top:
        # título = quién/qué conversación; cuerpo = el texto (en grupo, "Autor: texto")
        titulo = f"💬 {top['titulo']}"
        cuerpo = (f"{top['autor']}: {top['texto']}" if top["grupo"] else top["texto"]).strip()
        cuerpo = cuerpo[:140] or "Te escribió por Talk"
        if nuevos > 1:
            cuerpo = f"{cuerpo}  ·  +{nuevos - 1} más"
        try:
            # deep-link a ESA conversación dentro del workspace (?talk=<token>)
            enviar_push(db, row.user_id, titulo, cuerpo, url=f"/?talk={top['token']}", tag="gcf-talk")
        except Exception:
            pass

    if cambiado:
        row.talk_seen = json.dumps(seen)
        # OJO: no tocar updated_at (es la heurística de "app activa")
        db.commit()


async def _barrido() -> None:
    from app.api.v1.workspace import _talk  # import perezoso (evita ciclo)
    db = SessionLocal()
    try:
        ahora = utc_now()
        # Se notifica SIEMPRE que haya un mensaje nuevo y el token siga vivo, sin
        # importar si tienes otro dispositivo abierto (p.ej. el PC): el heurístico
        # de "app cerrada" era por-usuario (una sola fila) y no distinguía "celu
        # cerrado pero PC abierto", así que suprimía el push que sí quieres al celu.
        rows = (db.query(WorkspaceUserToken)
                .filter(WorkspaceUserToken.expires_at > ahora)
                .all())
        if not rows:
            return
        subs = {r[0] for r in db.query(WorkspacePushSubscription.user_id).distinct().all()}
        for row in rows:
            if row.user_id not in subs:
                continue
            try:
                await _procesar_usuario(db, _talk, row)
            except Exception:
                db.rollback()
    finally:
        db.close()


async def bucle_talk_push() -> None:
    if not (push_habilitado() and cifrado_disponible()):
        print("[talk-push] desactivado (falta VAPID o PUSH_TOKEN_KEY)")
        return
    while True:
        try:
            await _barrido()
        except Exception:
            print("[talk-push] error en barrido:\n" + traceback.format_exc())
        await asyncio.sleep(INTERVALO_S)
