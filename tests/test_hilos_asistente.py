"""Historial de conversación del asistente: hilos, mensajes y borrado.

Cubre las tres cosas que, si se rompen, no lanzan ninguna excepción y solo se
notan cuando ya hay datos mal guardados:

1. Al borrar un hilo se van sus mensajes. Hay DOS caminos de borrado —el ORM
   (`db.delete(hilo)`, que usa el cascade de la relación) y el masivo
   (`query(...).delete()`, que se apoya en el ON DELETE CASCADE de la FK)— y el
   segundo solo funciona si el motor tiene las FK activas. En MySQL lo están; en
   SQLite hay que encenderlas, y por eso el fixture las enciende: sin ese PRAGMA
   el test pasaría dejando mensajes huérfanos que en producción sí se borran, o
   al revés.
2. `created_at` contiene UTC. `DateTime(timezone=True)` compila a un DATETIME a
   secas y el driver descarta el tzinfo sin convertir, así que lo único que hace
   que la columna sea UTC es que se escriba con `utc_now()`.
3. El título se normaliza en la puerta. Es lo que la persona ve en la lista para
   reconocer una conversación, y un salto de línea o 300 caracteres la rompen.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.api.v1.workspace_assistant import LARGO_TITULO, _titulo_limpio
from app.core.datetime_utils import utc_now
from app.db.database import Base
from app.db.models import User, WorkspaceAssistantMessage, WorkspaceAssistantThread


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    # SQLite trae las FK apagadas por defecto. Sin esto, el borrado masivo dejaría
    # mensajes huérfanos aquí y no en MySQL, que es justo el desacuerdo que este
    # archivo existe para detectar.
    @event.listens_for(engine, "connect")
    def _encender_fks(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        # Sin drop_all: con las FK encendidas no puede ordenar el ciclo
        # teams↔users y revienta el teardown. La base vive en memoria y muere
        # con la conexión, así que no queda nada que limpiar.
        session.close()
        engine.dispose()


def _usuario(db, nombre="ana"):
    u = User(nc_user_id=f"u-{nombre}", display_name=nombre, email=f"{nombre}@gcf.group")
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _hilo_con_turnos(db, usuario, titulo="Agenda de mañana", turnos=2):
    h = WorkspaceAssistantThread(
        usuario_id=usuario.id, titulo=titulo,
        created_at=utc_now(), updated_at=utc_now(),
    )
    db.add(h)
    db.commit()
    db.refresh(h)
    for i in range(turnos):
        db.add(WorkspaceAssistantMessage(
            hilo_id=h.id,
            rol="usuario" if i % 2 == 0 else "asistente",
            contenido=f"turno {i}",
            origen="texto",
            created_at=utc_now(),
        ))
    db.commit()
    return h


# ===== BORRADO =====

def test_dado_un_hilo_con_mensajes_cuando_se_borra_por_orm_entonces_no_quedan_mensajes(db_session):
    usuario = _usuario(db_session)
    hilo = _hilo_con_turnos(db_session, usuario, turnos=3)
    assert db_session.query(WorkspaceAssistantMessage).count() == 3

    db_session.delete(hilo)
    db_session.commit()

    assert db_session.query(WorkspaceAssistantThread).count() == 0
    assert db_session.query(WorkspaceAssistantMessage).count() == 0


def test_dado_varios_hilos_cuando_se_borran_en_masa_entonces_tampoco_quedan_mensajes(db_session):
    """El camino de 'limpiar todo': un DELETE masivo que no instancia los hilos,
    así que el cascade que actúa es el de la FK, no el de la relación."""
    usuario = _usuario(db_session)
    _hilo_con_turnos(db_session, usuario, titulo="uno", turnos=2)
    _hilo_con_turnos(db_session, usuario, titulo="dos", turnos=4)
    assert db_session.query(WorkspaceAssistantMessage).count() == 6

    borrados = (
        db_session.query(WorkspaceAssistantThread)
        .filter(WorkspaceAssistantThread.usuario_id == usuario.id)
        .delete(synchronize_session=False)
    )
    db_session.commit()

    assert borrados == 2
    assert db_session.query(WorkspaceAssistantMessage).count() == 0


def test_dado_dos_personas_cuando_una_borra_todo_entonces_la_otra_conserva_sus_hilos(db_session):
    ana = _usuario(db_session, "ana")
    beto = _usuario(db_session, "beto")
    _hilo_con_turnos(db_session, ana, titulo="de ana", turnos=2)
    hilo_beto = _hilo_con_turnos(db_session, beto, titulo="de beto", turnos=2)

    (
        db_session.query(WorkspaceAssistantThread)
        .filter(WorkspaceAssistantThread.usuario_id == ana.id)
        .delete(synchronize_session=False)
    )
    db_session.commit()

    quedan = db_session.query(WorkspaceAssistantThread).all()
    assert [h.id for h in quedan] == [hilo_beto.id]
    assert db_session.query(WorkspaceAssistantMessage).count() == 2


# ===== HORAS =====

def test_dado_un_turno_guardado_cuando_se_relee_entonces_la_hora_es_utc(db_session):
    usuario = _usuario(db_session)
    hilo = _hilo_con_turnos(db_session, usuario, turnos=0)
    antes = datetime.now(timezone.utc).replace(tzinfo=None)
    db_session.add(WorkspaceAssistantMessage(
        hilo_id=hilo.id, rol="usuario", contenido="hola",
        origen="voz", created_at=utc_now(),
    ))
    db_session.commit()
    despues = datetime.now(timezone.utc).replace(tzinfo=None)

    guardado = db_session.execute(select(WorkspaceAssistantMessage)).scalar_one()
    crudo = guardado.created_at.replace(tzinfo=None)
    # Margen de un segundo por el redondeo del motor, no por incertidumbre de zona:
    # si la fila tuviera hora local en vez de UTC, la diferencia sería de horas.
    assert antes - timedelta(seconds=1) <= crudo <= despues + timedelta(seconds=1)


def test_dado_un_turno_dictado_cuando_se_guarda_entonces_conserva_su_origen(db_session):
    usuario = _usuario(db_session)
    hilo = _hilo_con_turnos(db_session, usuario, turnos=0)
    db_session.add(WorkspaceAssistantMessage(
        hilo_id=hilo.id, rol="usuario", contenido="dictado",
        origen="voz", created_at=utc_now(),
    ))
    db_session.commit()

    assert db_session.execute(select(WorkspaceAssistantMessage)).scalar_one().origen == "voz"


# ===== TÍTULO =====

def test_dado_un_titulo_con_saltos_de_linea_cuando_se_limpia_entonces_queda_en_una_sola_linea():
    assert _titulo_limpio("  agenda\n  de   mañana \n") == "agenda de mañana"


def test_dado_un_titulo_larguisimo_cuando_se_limpia_entonces_se_recorta_con_puntos_suspensivos():
    limpio = _titulo_limpio("x" * 400)
    assert len(limpio) == LARGO_TITULO
    assert limpio.endswith("…")


def test_dado_un_titulo_justo_en_el_limite_cuando_se_limpia_entonces_no_se_recorta():
    """El límite exacto no lleva puntos suspensivos: recortar aquí prometería un
    resto de frase que no existe."""
    exacto = "x" * LARGO_TITULO
    assert _titulo_limpio(exacto) == exacto


def test_dado_un_titulo_de_solo_espacios_cuando_se_limpia_entonces_queda_vacio():
    """El endpoint lo convierte en un 422: un hilo sin título no se reconoce en
    la lista, y ese es el único sitio desde el que se abre."""
    assert _titulo_limpio("   \n\t  ") == ""
