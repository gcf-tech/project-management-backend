"""Envío de correos de notificación (no-reply) vía SMTP. Best-effort: cualquier
fallo se registra y se ignora, para no romper la petición del usuario."""
import asyncio
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Optional

from app.core import config


def _send_sync(to: str, subject: str, html: str, text: Optional[str] = None) -> bool:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((config.SMTP_FROM_NAME, config.SMTP_FROM))
    msg["To"] = to
    if text:
        msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    ctx = ssl.create_default_context()
    if config.SMTP_PORT == 465:
        with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, context=ctx, timeout=15) as s:
            s.login(config.SMTP_USER, config.SMTP_PASSWORD)
            s.sendmail(config.SMTP_FROM, [to], msg.as_string())
    else:  # STARTTLS (p.ej. puerto 587)
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as s:
            s.starttls(context=ctx)
            s.login(config.SMTP_USER, config.SMTP_PASSWORD)
            s.sendmail(config.SMTP_FROM, [to], msg.as_string())
    return True


async def send_email(to: str, subject: str, html: str, text: Optional[str] = None) -> bool:
    """Envía un correo sin bloquear el event loop. Devuelve False si el email
    está deshabilitado o si el envío falla."""
    if not config.EMAIL_ENABLED or not to:
        return False
    try:
        return await asyncio.to_thread(_send_sync, to, subject, html, text)
    except Exception as e:  # best-effort
        print(f"[email] fallo enviando a {to}: {e}")
        return False


# ── Plantilla de correo de notificación de Deck ─────────────────────────────

_SUBJECTS = {
    "assigned":     "Te asignaron una tarjeta en Deck",
    "mentioned":    "Te mencionaron en Deck",
    "comment":      "Nuevo comentario en una tarjeta de Deck",
    "shared":       "Compartieron una tarjeta con tu equipo",
    "card_updated": "Actualización en una tarjeta de Deck",
    "moved":        "Una tarjeta cambió de estado",
    "due_soon":     "Una tarjeta vence pronto",
}


def build_notification_email(recipient_name: str, ntype: str, message: str,
                             card_title: Optional[str]) -> tuple[str, str, str]:
    """Devuelve (subject, html, text) para una notificación de Deck."""
    subject = _SUBJECTS.get(ntype, "Notificación de Deck")
    card_line = f'<p style="margin:0 0 6px;color:#5a6473;font-size:14px;">Tarjeta: <b style="color:#1d2129;">{card_title}</b></p>' if card_title else ""
    url = config.DECK_APP_URL
    html = f"""\
<!doctype html><html><body style="margin:0;background:#f4f6fa;padding:24px;font-family:Inter,Arial,sans-serif;">
  <div style="max-width:520px;margin:0 auto;background:#fff;border:1px solid #dde3ec;border-radius:14px;overflow:hidden;">
    <div style="background:#1d2129;padding:16px 22px;color:#fff;font-weight:800;font-size:16px;">
      <span style="color:#F37022;">Deck</span> · GCF
    </div>
    <div style="padding:22px;">
      <p style="margin:0 0 10px;font-size:15px;color:#1c2430;">Hola {recipient_name or ''},</p>
      <p style="margin:0 0 12px;font-size:15px;color:#1c2430;">{message}</p>
      {card_line}
      <a href="{url}" style="display:inline-block;margin-top:14px;background:#F37022;color:#fff;text-decoration:none;font-weight:700;padding:10px 18px;border-radius:9px;font-size:14px;">Abrir en Deck</a>
    </div>
    <div style="padding:14px 22px;border-top:1px solid #eef1f6;color:#8a93a3;font-size:12px;">
      Notificación automática · no respondas a este correo.
    </div>
  </div>
</body></html>"""
    text = f"{message}\n" + (f"Tarjeta: {card_title}\n" if card_title else "") + f"\nAbrir en Deck: {url}\n\nNotificación automática, no respondas a este correo."
    return subject, html, text
