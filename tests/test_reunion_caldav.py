"""Tests del espejo CalDAV de las reuniones del workspace.

`POST /reuniones` escribe en dos sitios (MySQL + un .ics en Nextcloud). Durante
un tiempo la baja borró solo en uno y la copia sobrevivía en el drawer de
Calendario sin fila que la explicara. Estos tests fijan los invariantes que
hacen que el espejo siga siendo un espejo:

  1. Las tres operaciones (alta, reemisión, borrado) construyen LA MISMA URL.
     Es toda la premisa del diseño: el UID es determinista, así que no hay que
     buscar el evento — pero solo mientras las tres coincidan.
  2. Un 404 al borrar es ÉXITO. Cancelar dos veces no puede reportar un fallo
     que no existe.
  3. El borrado nunca lanza: devuelve en qué acabó, para que quien llama lo
     reporte en vez de tragárselo.
  4. Un datetime naive se escribe como UTC, no como hora del servidor. Al
     reemitir, los valores vienen de MySQL y pueden llegar sin tzinfo: asumir
     la zona del servidor movería la reunión cinco horas.
  5. La reemisión incrementa SEQUENCE leyéndolo del propio recurso (no hay
     columna donde guardarlo y este cambio no trae migración).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import app.api.v1.workspace as ws


UTC = timezone.utc


# ── Doble de httpx ──────────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


class _Registro:
    """Guarda lo que se pidió y decide qué responder."""

    def __init__(self):
        self.llamadas: list[tuple[str, str]] = []   # (metodo, url)
        self.contenidos: list[bytes] = []
        self.respuestas: dict[str, _FakeResponse] = {}
        self.explota: str | None = None             # método que lanza

    def responder(self, metodo: str, status: int, text: str = ""):
        self.respuestas[metodo] = _FakeResponse(status, text)


@pytest.fixture
def reg(monkeypatch):
    registro = _Registro()

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def _op(self, metodo, url, headers=None, content=None):
            if registro.explota == metodo:
                raise RuntimeError("Nextcloud no responde")
            registro.llamadas.append((metodo, url))
            if content is not None:
                registro.contenidos.append(content)
            return registro.respuestas.get(metodo, _FakeResponse(204))

        async def put(self, url, headers=None, content=None):
            return await self._op("put", url, headers, content)

        async def delete(self, url, headers=None):
            return await self._op("delete", url, headers)

        async def get(self, url, headers=None):
            return await self._op("get", url, headers)

    monkeypatch.setattr(ws.httpx, "AsyncClient", _FakeClient)
    return registro


# ── 1. La misma URL en las tres operaciones ─────────────────────────────────

@pytest.mark.asyncio
async def test_alta_reemision_y_borrado_usan_la_misma_url(reg):
    """Si estas tres se separan, el borrado deja de encontrar lo que creó el alta
    y vuelve el evento fantasma. Es el invariante que sostiene la opción A."""
    inicio = datetime(2026, 8, 18, 20, 0, tzinfo=UTC)
    fin = inicio + timedelta(hours=1)

    await ws._crear_evento_caldav("Bearer t", "mmazo", "gcfws-13", "Prueba", inicio, fin)
    await ws._actualizar_evento_caldav("Bearer t", "mmazo", "gcfws-13", "Prueba", inicio, fin)
    await ws._borrar_evento_caldav("Bearer t", "mmazo", "gcfws-13")

    urls = {url for _, url in reg.llamadas}
    assert len(urls) == 1, f"las operaciones divergieron: {urls}"
    assert urls.pop().endswith("/calendars/mmazo/personal/gcfws-13.ics")


def test_la_url_escapa_el_usuario_y_el_uid():
    """Un nc_user_id con caracteres raros no puede romper la ruta ni escaparse
    del calendario del propio usuario."""
    url = ws._url_evento_caldav("a/b c", "gcfws-1")
    assert "a%2Fb%20c" in url
    assert "/personal/gcfws-1.ics" in url


# ── 2 y 3. El borrado: qué cuenta como éxito y qué nunca lanza ──────────────

@pytest.mark.parametrize("status,esperado", [
    (204, True),   # borrado normal
    (200, True),
    (202, True),
    (404, True),   # ya no estaba: es justo lo que se pedía
    (403, False),  # el token no puede tocar ese calendario
    (500, False),  # Nextcloud caído
])
@pytest.mark.asyncio
async def test_borrado_traduce_el_status(reg, status, esperado):
    reg.responder("delete", status)
    assert await ws._borrar_evento_caldav("Bearer t", "mmazo", "gcfws-13") is esperado


@pytest.mark.asyncio
async def test_borrar_dos_veces_no_reporta_error(reg):
    """Segunda cancelación de la misma reunión: Nextcloud responde 404 y eso NO
    puede llegar a la tarjeta como 'quedó una copia'."""
    reg.responder("delete", 204)
    assert await ws._borrar_evento_caldav("Bearer t", "mmazo", "gcfws-13") is True
    reg.responder("delete", 404)
    assert await ws._borrar_evento_caldav("Bearer t", "mmazo", "gcfws-13") is True


@pytest.mark.asyncio
async def test_borrado_no_lanza_si_nextcloud_no_responde(reg):
    """Best-effort: la cancelación no se cae porque Nextcloud esté caído. Pero
    devuelve False para que quien llama lo diga, en vez de tragárselo."""
    reg.explota = "delete"
    assert await ws._borrar_evento_caldav("Bearer t", "mmazo", "gcfws-13") is False


# ── 4. Naive = UTC, no hora del servidor ────────────────────────────────────

def test_un_naive_se_escribe_como_utc():
    """Al reemitir, `m.inicio` sale de MySQL y puede venir sin tzinfo. Con
    `astimezone()` a secas Python asumiría la zona del servidor y en Bogotá la
    reunión se movería cinco horas."""
    assert ws._fmt_ics(datetime(2026, 8, 18, 20, 0)) == "20260818T200000Z"


def test_un_aware_se_convierte_a_utc():
    bogota = timezone(timedelta(hours=-5))
    assert ws._fmt_ics(datetime(2026, 8, 18, 15, 0, tzinfo=bogota)) == "20260818T200000Z"


# ── 5. SEQUENCE: el alta en 0, la reemisión +1 ──────────────────────────────

def test_el_ics_lleva_los_campos_minimos():
    inicio = datetime(2026, 8, 18, 20, 0, tzinfo=UTC)
    ics = ws._vevent_ics("gcfws-13", "Prueba", inicio, inicio + timedelta(hours=1), "Meet", 0).decode()
    assert "UID:gcfws-13@gcf-workspace" in ics
    assert "DTSTART:20260818T200000Z" in ics
    assert "DTEND:20260818T210000Z" in ics
    assert "SEQUENCE:0" in ics
    assert "DESCRIPTION:Meet" in ics
    # Sin STATUS a propósito: por eso el filtro de calendar-panel.js nunca los ve
    # y por eso hace falta el DELETE de verdad.
    assert "STATUS:" not in ics


@pytest.mark.asyncio
async def test_la_reemision_incrementa_el_sequence_leido(reg):
    reg.responder("get", 200, "BEGIN:VCALENDAR\r\nSEQUENCE:4\r\nEND:VCALENDAR\r\n")
    inicio = datetime(2026, 8, 18, 20, 0, tzinfo=UTC)
    await ws._actualizar_evento_caldav("Bearer t", "mmazo", "gcfws-13", "Prueba", inicio, inicio)
    assert "SEQUENCE:5" in reg.contenidos[-1].decode()


@pytest.mark.asyncio
async def test_si_el_ics_no_se_puede_leer_el_sequence_arranca_en_1(reg):
    """404 en el GET (la copia no está) o un cuerpo sin SEQUENCE: se parte de 0 y
    se sube a 1. Arriesga que un cliente ignore la actualización, nunca que se
    pierda el evento."""
    reg.responder("get", 404)
    inicio = datetime(2026, 8, 18, 20, 0, tzinfo=UTC)
    await ws._actualizar_evento_caldav("Bearer t", "mmazo", "gcfws-13", "Prueba", inicio, inicio)
    assert "SEQUENCE:1" in reg.contenidos[-1].decode()
