"""i18n del backend para textos generados hacia el usuario (notificaciones,
correos, reporte). El idioma se resuelve por `user.lang` ('es' | 'en'); si es
NULL se asume 'es'. Uso: t(lang, 'notif.assigned', actor='Ana', target='Luis').

Mantener AMBOS idiomas sincronizados. El español es el idioma base (fallback).
"""
from typing import Any

SUPPORTED = ("es", "en")

_ES: dict[str, str] = {
    # Notificaciones (mensaje corto de la campana / push / correo)
    "notif.assigned":    "{actor} asignó a {target}",
    "notif.moved":       "{actor} movió la tarjeta a {stage}",
    "notif.comment":     "{actor} comentó",
    "notif.card_updated": "{actor} actualizó la tarjeta",
    "notif.due_changed": "{actor} cambió la fecha de vencimiento",
    "notif.completed":   "{actor} completó la tarjeta",
    "notif.shared":      "{actor} compartió la tarjeta con {team}",
    "notif.subtask_done": "Subtarea completada: «{title}»",
    "notif.due_soon":    "«{title}» vence pronto",
    # Correo de notificación (envoltura)
    "email.subject.assigned":     "Te asignaron una tarjeta en Deck",
    "email.subject.mentioned":    "Te mencionaron en Deck",
    "email.subject.comment":      "Nuevo comentario en una tarjeta de Deck",
    "email.subject.shared":       "Compartieron una tarjeta con tu equipo",
    "email.subject.card_updated": "Actualización en una tarjeta de Deck",
    "email.subject.moved":        "Una tarjeta cambió de estado",
    "email.subject.due_soon":     "Una tarjeta vence pronto",
    "email.subject.default":      "Notificación de Deck",
    "email.greeting":  "Hola {name},",
    "email.cardLabel": "Tarjeta:",
    "email.open":      "Abrir en Deck",
    "email.footer":    "Notificación automática · no respondas a este correo.",
    "email.fallbackBody": "Tienes una actualización en Deck.",
    # Reporte semanal
    "report.subject":   "Reporte de Deck · {period}",
    "report.header":    "Reporte semanal",
    "report.summaryOf": "Resumen de {period}",
    "report.scope.all": "Todos los equipos",
    "report.testPrefix": "[Prueba] ",
    "report.line":      "- {team}: {completed} compl., {created} nuevas, {inProgress} en curso, {overdue} vencidas.",
    "report.col.team":       "Equipo",
    "report.col.completed":  "Compl.",
    "report.col.created":    "Nuevas",
    "report.col.inProgress": "En curso",
    "report.col.overdue":    "Vencidas",
    "report.col.cycle":      "Ciclo",
    "report.col.bottleneck": "Cuello de botella",
    "report.totals":     "Totales:",
    "report.completedW": "completadas",
    "report.createdW":   "nuevas",
    "report.inProgressW": "en curso",
    "report.overdueW":   "vencidas",
    "report.open":       "Abrir el dashboard",
    "report.footer":     "Reporte automático de los lunes · no respondas a este correo.",
    "report.period.week":   "la última semana",
    "report.period.biweek": "las últimas 2 semanas",
    "report.period.month":  "el último mes",
}

_EN: dict[str, str] = {
    "notif.assigned":    "{actor} assigned {target}",
    "notif.moved":       "{actor} moved the card to {stage}",
    "notif.comment":     "{actor} commented",
    "notif.card_updated": "{actor} updated the card",
    "notif.due_changed": "{actor} changed the due date",
    "notif.completed":   "{actor} completed the card",
    "notif.shared":      "{actor} shared the card with {team}",
    "notif.subtask_done": "Subtask completed: “{title}”",
    "notif.due_soon":    "“{title}” is due soon",
    "email.subject.assigned":     "You were assigned a card in Deck",
    "email.subject.mentioned":    "You were mentioned in Deck",
    "email.subject.comment":      "New comment on a Deck card",
    "email.subject.shared":       "A card was shared with your team",
    "email.subject.card_updated": "Update on a Deck card",
    "email.subject.moved":        "A card changed stage",
    "email.subject.due_soon":     "A card is due soon",
    "email.subject.default":      "Deck notification",
    "email.greeting":  "Hi {name},",
    "email.cardLabel": "Card:",
    "email.open":      "Open in Deck",
    "email.footer":    "Automated notification · do not reply to this email.",
    "email.fallbackBody": "You have an update in Deck.",
    "report.subject":   "Deck report · {period}",
    "report.header":    "Weekly report",
    "report.summaryOf": "Summary of {period}",
    "report.scope.all": "All teams",
    "report.testPrefix": "[Test] ",
    "report.line":      "- {team}: {completed} done, {created} new, {inProgress} in progress, {overdue} overdue.",
    "report.col.team":       "Team",
    "report.col.completed":  "Done",
    "report.col.created":    "New",
    "report.col.inProgress": "In progress",
    "report.col.overdue":    "Overdue",
    "report.col.cycle":      "Cycle",
    "report.col.bottleneck": "Bottleneck",
    "report.totals":     "Totals:",
    "report.completedW": "completed",
    "report.createdW":   "new",
    "report.inProgressW": "in progress",
    "report.overdueW":   "overdue",
    "report.open":       "Open the dashboard",
    "report.footer":     "Automated Monday report · do not reply to this email.",
    "report.period.week":   "the last week",
    "report.period.biweek": "the last 2 weeks",
    "report.period.month":  "the last month",
}

_DICTS = {"es": _ES, "en": _EN}


def norm_lang(lang: Any) -> str:
    l = (lang or "es")
    l = str(l).strip().lower()[:2]
    return l if l in SUPPORTED else "es"


def t(lang: Any, key: str, **vars) -> str:
    lang = norm_lang(lang)
    val = _DICTS[lang].get(key)
    if val is None:
        val = _ES.get(key, key)   # fallback al español y luego a la clave
    try:
        return val.format(**vars) if vars else val
    except (KeyError, IndexError):
        return val


def period_label(range_days: int, lang: Any) -> str:
    if range_days <= 7:
        return t(lang, "report.period.week")
    if range_days <= 14:
        return t(lang, "report.period.biweek")
    return t(lang, "report.period.month")
