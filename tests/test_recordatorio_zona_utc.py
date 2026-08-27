"""Recordatorios del asistente: la columna `vence_en` contiene UTC, siempre.

Esta es la red que faltaba bajo una convención que hasta ahora se sostenía sola.
El motor NO garantiza nada: `DateTime(timezone=True)` compila a un `DATETIME` a
secas en MySQL, que no almacena offset, y el driver **descarta el tzinfo sin
convertir** (ver test_el_driver_descarta_el_tzinfo). Lo único que hace que la
columna contenga UTC es que `ensure_aware_utc()` corra antes de cada escritura.

Si alguien quita esa llamada, o añade un tercer camino de escritura que se la
salte, la fila guardará hora local rotulada como UTC. No falla nada, no salta
ninguna excepción: simplemente el recordatorio suena a la hora equivocada, y a
posteriori es indetectable. De ahí este archivo.

La zona horaria de la fila es un METADATO y no interviene en nada de esto: no
reinterpreta `vence_en` ni entra en el barrido del scheduler.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.datetime_utils import ensure_aware_utc
from app.db.database import Base
from app.db.models import User, WorkspaceAssistantReminder

# Madrid en agosto. Se elige un offset positivo a propósito: con +02:00 la hora de
# pared (09:00) y el instante UTC (07:00) caen en horas distintas y el mismo día,
# así que una confusión entre ambos se ve en el aserto en vez de compensarse.
MADRID_VERANO = timezone(timedelta(hours=2))


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


@pytest.fixture()
def seed_user(db_session):
    user = User(id=1, nc_user_id="u-tz", display_name="Persona en Madrid")
    db_session.add(user)
    db_session.commit()
    return user


def _crear_como_el_endpoint(db_session, user, iso_aware, zona=None):
    """El mismo orden de operaciones que `crear_recordatorio`: normalizar a UTC
    primero y construir la fila después.

    No se llama al endpoint por HTTP porque `_resolve_user` sale a Nextcloud a
    validar el token, y eso convertiría un test de zonas en un test de red. Lo
    que aquí importa —que la normalización ocurra ANTES de tocar la sesión— es
    exactamente lo que se reproduce.
    """
    r = WorkspaceAssistantReminder(
        usuario_id=user.id,
        texto="Llamar al banco",
        vence_en=ensure_aware_utc(iso_aware),
        estado="pendiente",
        created_at=datetime.now(timezone.utc),
    )
    if zona:
        r.zona_horaria = zona
    db_session.add(r)
    db_session.commit()
    db_session.expire_all()      # fuerza releer de la base, no del identity map
    return db_session.get(WorkspaceAssistantReminder, r.id)


# ---------------------------------------------------------------------------
# El filo: por qué la normalización es imprescindible
# ---------------------------------------------------------------------------

def test_el_driver_descarta_el_tzinfo_sin_convertir():
    """PyMySQL guarda la hora de PARED y tira el offset.

    Un aware `09:00+02:00` NO se guarda como el instante `07:00Z`: se guarda como
    `09:00`. Este test no comprueba nuestro código, documenta el terreno sobre el
    que está construido — y avisará si una versión futura del driver cambia de
    criterio, que también sería una noticia.
    """
    from pymysql.converters import escape_datetime

    utc = datetime(2026, 8, 25, 7, 0, tzinfo=timezone.utc)
    madrid = datetime(2026, 8, 25, 9, 0, tzinfo=MADRID_VERANO)

    assert escape_datetime(utc, {}) == "'2026-08-25 07:00:00'"
    # El mismo INSTANTE que el de arriba, y sin embargo se escribe distinto:
    assert escape_datetime(madrid, {}) == "'2026-08-25 09:00:00'"


def test_el_arnes_reproduce_el_mismo_filo(db_session, seed_user):
    """La trampa no es exclusiva de MySQL: SQLite se comporta igual.

    Escribir el aware SIN normalizar guarda 09:00 donde debía ir 07:00. Es el
    escenario que `ensure_aware_utc()` evita, y lo dejamos escrito para que nadie
    concluya que en el arnés "da igual" porque el motor sea otro.
    """
    sin_normalizar = WorkspaceAssistantReminder(
        usuario_id=seed_user.id,
        texto="Sin normalizar",
        vence_en=datetime(2026, 8, 25, 9, 0, tzinfo=MADRID_VERANO),
        estado="pendiente",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(sin_normalizar)
    db_session.commit()

    crudo = db_session.execute(
        text("SELECT vence_en FROM workspace_assistant_reminders WHERE id = :i"),
        {"i": sin_normalizar.id},
    ).scalar_one()
    assert str(crudo).startswith("2026-08-25 09:00"), (
        "el driver guardó la hora de pared, que es justo lo que hay que prevenir"
    )


# ---------------------------------------------------------------------------
# La garantía: el camino del endpoint sí deja UTC en la columna
# ---------------------------------------------------------------------------

def test_offset_no_utc_queda_en_la_columna_como_instante_utc(db_session, seed_user):
    """"Recuérdame mañana a las 9" dicho desde Madrid → 07:00 UTC en la fila."""
    pedido = datetime(2026, 8, 25, 9, 0, tzinfo=MADRID_VERANO)
    r = _crear_como_el_endpoint(db_session, seed_user, pedido, zona="Europe/Madrid")

    # Lo que hay en la columna son componentes UTC, no la hora de pared de Madrid.
    assert (r.vence_en.year, r.vence_en.month, r.vence_en.day) == (2026, 8, 25)
    assert (r.vence_en.hour, r.vence_en.minute) == (7, 0)

    # Y sigue siendo el MISMO instante que se pidió: nada se perdió por el camino.
    guardado_utc = r.vence_en.replace(tzinfo=timezone.utc)
    assert guardado_utc == pedido


def test_el_scheduler_lo_encuentra_a_la_hora_local_correcta(db_session, seed_user):
    """El bucle completo: se pide a las 9 de Madrid y vence a las 9 de Madrid.

    Reproduce el filtro real del scheduler (`estado == pendiente` y
    `vence_en <= ahora`, resuelto en SQL) a un lado y otro del instante exacto.
    Si alguien rompe la normalización, este test falla: con la hora de pared
    guardada, a las 07:00Z el recordatorio todavía no aparecería.
    """
    pedido = datetime(2026, 8, 25, 9, 0, tzinfo=MADRID_VERANO)   # = 07:00Z
    _crear_como_el_endpoint(db_session, seed_user, pedido, zona="Europe/Madrid")

    def vencidos_a(momento_utc):
        return (
            db_session.query(WorkspaceAssistantReminder)
            .filter(
                WorkspaceAssistantReminder.estado == "pendiente",
                WorkspaceAssistantReminder.vence_en <= momento_utc,
            )
            .count()
        )

    un_minuto_antes = datetime(2026, 8, 25, 6, 59, tzinfo=timezone.utc)
    justo_a_la_hora = datetime(2026, 8, 25, 7, 0, tzinfo=timezone.utc)

    assert vencidos_a(un_minuto_antes) == 0, "sonó antes de tiempo"
    assert vencidos_a(justo_a_la_hora) == 1, "no sonó a las 9 de Madrid"


# ---------------------------------------------------------------------------
# La zona: metadato, con default, validada en la puerta
# ---------------------------------------------------------------------------

def test_sin_zona_se_persiste_el_default(db_session, seed_user):
    """Un cliente que no manda zona deja la fila igual que las de antes."""
    r = _crear_como_el_endpoint(
        db_session, seed_user, datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc),
    )
    assert r.zona_horaria == "America/Bogota"


def test_la_zona_no_mueve_el_instante(db_session, seed_user):
    """Dos recordatorios con el MISMO instante y distinta zona vencen a la vez.

    Es la definición de "metadato": la columna describe con qué reloj se pidió,
    no cambia cuándo suena.
    """
    instante = datetime(2026, 8, 25, 7, 0, tzinfo=timezone.utc)
    a = _crear_como_el_endpoint(db_session, seed_user, instante, zona="Europe/Madrid")
    b = _crear_como_el_endpoint(db_session, seed_user, instante, zona="Asia/Tokyo")

    assert a.zona_horaria != b.zona_horaria
    assert a.vence_en == b.vence_en


@pytest.mark.parametrize("zona", ["no/existe", "Europa/Madrid", "/etc/passwd", "Bogota"])
def test_zona_invalida_es_422(zona):
    """Una zona inventada se rechaza en la puerta, no se guarda en silencio.

    Guardarla sin mirar no rompe nada hasta que alguien intenta re-renderizar con
    ella, y para entonces ya no se sabe cuál era la buena. Mismo criterio que el
    offset ausente, que ya devuelve 422.
    """
    from fastapi import HTTPException

    from app.api.v1.workspace_assistant import _zona_o_422

    with pytest.raises(HTTPException) as exc:
        _zona_o_422(zona)
    assert exc.value.status_code == 422


@pytest.mark.parametrize("valor", [None, "", "   "])
def test_zona_ausente_no_es_error(valor):
    """Ausente no es inválido: devuelve None y manda el default de la columna."""
    from app.api.v1.workspace_assistant import _zona_o_422

    assert _zona_o_422(valor) is None


@pytest.mark.parametrize("zona", ["America/Bogota", "Europe/Madrid", "Asia/Tokyo", "UTC"])
def test_zonas_validas_pasan(zona):
    from app.api.v1.workspace_assistant import _zona_o_422

    assert _zona_o_422(zona) == zona
