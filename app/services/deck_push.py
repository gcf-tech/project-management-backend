"""Barrido en segundo plano para el push nativo de 'due_soon' del Deck.

Las notis de menciones se envían en el momento (al postear el comentario), pero
las de 'card pronta a vencer' se generan de forma perezosa cuando el cliente pide
sus notificaciones (app abierta). Para que también lleguen con la app CERRADA,
este bucle recorre a los usuarios SUSCRITOS al push y les genera/empuja las
due_soon que falten. El dedup vive en _ensure_due_soon_notifications, así que no
duplica avisos aunque corra cada rato.
"""
import asyncio
import traceback

from app.db.database import SessionLocal
from app.db.models import User, WorkspacePushSubscription
from app.services.push import push_habilitado

INTERVALO_S = 20 * 60  # cada 20 minutos


def _barrido() -> int:
    # Import perezoso: evita import circular (deck.py importa muchísimo).
    from app.api.v1.deck import _ensure_due_soon_notifications
    db = SessionLocal()
    try:
        uids = [r[0] for r in db.query(WorkspacePushSubscription.user_id).distinct().all()]
        n = 0
        for uid in uids:
            user = db.query(User).filter(User.id == uid).first()
            if not user:
                continue
            try:
                _ensure_due_soon_notifications(db, user)  # crea + pushea lo que falte
                n += 1
            except Exception:
                db.rollback()
        return n
    finally:
        db.close()


async def bucle_push_deck() -> None:
    """Corre para siempre; un fallo puntual no tumba el bucle."""
    if not push_habilitado():
        print("[deck-push] push desactivado (sin VAPID): no se arranca el barrido due_soon")
        return
    while True:
        try:
            await asyncio.to_thread(_barrido)
        except Exception:
            print("[deck-push] error en barrido:\n" + traceback.format_exc())
        await asyncio.sleep(INTERVALO_S)
