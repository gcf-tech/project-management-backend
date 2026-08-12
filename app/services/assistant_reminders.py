"""
Scheduler de recordatorios del asistente por voz (Fase 2B).

Vive aquí y no en el Express del workspace, cuyo estado es volátil por diseño
(CONTEXT.md §5): la fuente de verdad es MySQL y barre quien tiene la conexión.

Entrega por el canal que ya existe, `deck_notifications` (la campana 🔔 de
notifications.js), con `card_id`/`actor_id` NULL y tipo 'assistant_reminder'.
"""
import asyncio
import traceback
from datetime import datetime

from sqlalchemy import and_

from app.db.database import SessionLocal
from app.db.models import WorkspaceAssistantReminder, DeckNotification
from app.core.datetime_utils import utc_now

INTERVALO_S = 60
# Tope por barrido: tras una caída larga se entregan en tandas, sin bloquear
# el loop ni la conexión durante minutos.
MAX_POR_BARRIDO = 200


def _barrer_una_vez() -> int:
    """Un barrido completo, síncrono. Devuelve cuántos recordatorios entregó.

    Se marca 'notificado' ANTES de crear la notificación a propósito: si el
    proceso muere a mitad se pierde uno, en vez de duplicarlo veinte veces.
    """
    db = SessionLocal()
    entregados = 0
    try:
        ahora = utc_now()
        vencidos = (
            db.query(WorkspaceAssistantReminder)
            .filter(
                and_(
                    WorkspaceAssistantReminder.estado == "pendiente",
                    WorkspaceAssistantReminder.vence_en <= ahora,
                )
            )
            .order_by(WorkspaceAssistantReminder.vence_en.asc())
            .limit(MAX_POR_BARRIDO)
            .all()
        )
        for r in vencidos:
            # Condicional: si otra réplica ya lo tomó, afecta 0 filas y se salta.
            tomados = (
                db.query(WorkspaceAssistantReminder)
                .filter(
                    WorkspaceAssistantReminder.id == r.id,
                    WorkspaceAssistantReminder.estado == "pendiente",
                )
                .update(
                    {"estado": "notificado", "notificado_en": ahora},
                    synchronize_session=False,
                )
            )
            if not tomados:
                continue
            db.commit()

            db.add(DeckNotification(
                user_id=r.usuario_id,
                actor_id=None,      # lo disparó el reloj, no una persona
                card_id=None,       # no es de Deck; sin cardId el cliente no navega
                activity_id=None,
                type="assistant_reminder",
                message=f"Recordatorio: {r.texto}"[:500],
                is_read=False,
                created_at=ahora,
            ))
            db.commit()
            entregados += 1
        return entregados
    except Exception:
        db.rollback()
        # Un barrido fallido no tumba el bucle: se reintenta al minuto siguiente.
        print("[assistant-reminders] fallo en el barrido:\n" + traceback.format_exc())
        return entregados
    finally:
        db.close()


async def bucle_recordatorios() -> None:
    """Bucle de fondo. Se lanza desde el lifespan de app/main.py."""
    print(f"[assistant-reminders] scheduler activo (cada {INTERVALO_S}s)")
    while True:
        try:
            # SQLAlchemy síncrono: va a un hilo para no bloquear el event loop.
            n = await asyncio.to_thread(_barrer_una_vez)
            if n:
                print(f"[assistant-reminders] {n} recordatorio(s) entregado(s)")
        except asyncio.CancelledError:
            print("[assistant-reminders] scheduler detenido")
            raise
        except Exception:
            print("[assistant-reminders] error inesperado:\n" + traceback.format_exc())
        await asyncio.sleep(INTERVALO_S)
