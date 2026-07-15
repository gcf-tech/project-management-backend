"""Ejecuta el reporte de Deck SI corresponde según la configuración admin
(frecuencia semanal/quincenal/mensual, día y hora). Pensado para un cron DIARIO:
el 'cuándo' vive en la config (editable por el admin), no en el cron.

    # crontab -e   (hora del servidor) — corre a diario; el script decide si toca
    0 8 * * *  cd /ruta/project-management-backend && ./venv/bin/python -m scripts.send_weekly_report >> /var/log/deck_report.log 2>&1

Uso manual:
    python -m scripts.send_weekly_report            # envía si corresponde hoy
    python -m scripts.send_weekly_report --force    # fuerza el envío ahora
    python -m scripts.send_weekly_report --dry       # simula (no envía)
"""
import asyncio
import sys

from app.db.database import SessionLocal
from app.api.v1.deck import run_scheduled_report


async def main():
    force = "--force" in sys.argv
    dry = "--dry" in sys.argv
    db = SessionLocal()
    try:
        res = await run_scheduled_report(db, force=force, dry=dry)
        if res.get("skipped"):
            print(f"[report] omitido: {res.get('reason')}")
        else:
            print(f"[report] dry={res.get('dry')} enviados={res.get('sent')} destinatarios={len(res.get('recipients', []))}")
            for r in res.get("recipients", []):
                print("  ", r)
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
