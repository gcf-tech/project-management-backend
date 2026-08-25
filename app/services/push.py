"""Web Push del workspace: envía notificaciones NATIVAS al celular vía las
suscripciones que guarda cada navegador (PWA), usando claves VAPID.

Si no hay claves VAPID configuradas, `push_habilitado()` es False y todo queda
como no-op (el workspace sigue funcionando, solo sin push nativo).
"""
import json
import logging

from py_vapid import Vapid
from pywebpush import webpush, WebPushException

from app.core import config
from app.db.models import WorkspacePushSubscription

log = logging.getLogger("workspace.push")

_vapid = None  # instancia Vapid cacheada (evita re-parsear la clave en cada envío)


def push_habilitado() -> bool:
    return bool(config.VAPID_PRIVATE_KEY and config.VAPID_PUBLIC_KEY)


def _get_vapid():
    global _vapid
    if _vapid is None:
        _vapid = Vapid.from_raw(config.VAPID_PRIVATE_KEY.encode())
    return _vapid


def enviar_push(db, user_id: int, title: str, body: str = "",
                url: str = "/", tag: str = "gcf-workspace") -> int:
    """Envía un push a TODAS las suscripciones del usuario. Devuelve cuántas
    recibieron. Las suscripciones muertas (404/410) se borran solas."""
    if not push_habilitado():
        return 0
    subs = (db.query(WorkspacePushSubscription)
            .filter(WorkspacePushSubscription.user_id == user_id).all())
    if not subs:
        return 0
    payload = json.dumps({"title": title, "body": body, "url": url, "tag": tag})
    claims = {"sub": config.VAPID_SUBJECT}
    enviados = 0
    muertas = []
    for s in subs:
        info = {"endpoint": s.endpoint, "keys": {"p256dh": s.p256dh, "auth": s.auth}}
        try:
            webpush(subscription_info=info, data=payload,
                    vapid_private_key=_get_vapid(), vapid_claims=dict(claims))
            enviados += 1
        except WebPushException as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (404, 410):
                muertas.append(s)  # suscripción caducada → limpiar
            else:
                log.warning("push falló (%s): %s", code, e)
        except Exception as e:
            log.warning("push error inesperado: %s", e)
    for s in muertas:
        db.delete(s)
    if muertas:
        db.commit()
    return enviados
