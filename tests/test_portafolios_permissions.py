"""Permisos de la herramienta de portafolios.

- Leer: cualquier usuario con role_commercial.
- Escribir (portafolios y rentabilidad): además, grupo PORTAFOLIOS_ADMIN_GROUP ("CEO").
- /auth/me expone los grupos de Nextcloud para que el frontend adapte la interfaz.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_db, require_user
from app.api.v1 import auth, portafolios
from app.db.models import Portafolio, PortafolioRentabilidad

AUTH = {"Authorization": "Bearer test-token"}
NUEVO = {"nombre": "Serenity", "montoMinimo": 20000, "rendimientoAnual": 10.99, "nivelRiesgo": 2}


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Portafolio.__table__.create(engine)
    PortafolioRentabilidad.__table__.create(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(portafolios.router, prefix="/api/portafolios")
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[require_user] = lambda: SimpleNamespace(
        nc_user_id="asesor1", role_commercial="asesor"
    )
    return TestClient(app)


def _groups(*names):
    return patch.object(portafolios, "get_nc_user_groups", AsyncMock(return_value=list(names)))


def test_commercial_without_ceo_can_read(client):
    with _groups("Tech"):
        r = client.get("/api/portafolios", headers=AUTH)
    assert r.status_code == 200


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/api/portafolios", NUEVO),
        ("patch", "/api/portafolios/1", {"nombre": "X"}),
        ("delete", "/api/portafolios/1", None),
        ("put", "/api/portafolios/1/rentabilidad/2026", {"meses": {"1": 1.0}}),
        ("delete", "/api/portafolios/1/rentabilidad/2026", None),
    ],
)
def test_writes_rejected_without_ceo_group(client, method, path, body):
    with _groups("Tech", "emploiee"):
        kwargs = {"headers": AUTH}
        if body is not None:
            kwargs["json"] = body
        r = getattr(client, method)(path, **kwargs)
    assert r.status_code == 403


def test_ceo_can_create_edit_and_load_performance(client):
    with _groups("CEO", "Tech"):
        r = client.post("/api/portafolios", json=NUEVO, headers=AUTH)
        assert r.status_code == 201
        pid = r.json()["id"]
        assert client.patch(f"/api/portafolios/{pid}", json={"orden": 2}, headers=AUTH).status_code == 200
        r = client.put(
            f"/api/portafolios/{pid}/rentabilidad/2026", json={"meses": {"1": 1.21}}, headers=AUTH
        )
        assert r.status_code == 200


def test_non_commercial_is_rejected_before_group_check(client):
    client.app.dependency_overrides[require_user] = lambda: SimpleNamespace(
        nc_user_id="otro", role_commercial=None
    )
    mock = AsyncMock(return_value=["CEO"])
    with patch.object(portafolios, "get_nc_user_groups", mock):
        r = client.post("/api/portafolios", json=NUEVO, headers=AUTH)
    assert r.status_code == 403
    mock.assert_not_called()


def test_auth_me_includes_nextcloud_groups():
    app = FastAPI()
    app.include_router(auth.router, prefix="/auth")
    app.dependency_overrides[get_db] = lambda: None
    nc = {"id": "sgutierrez", "displayname": "Santiago Gutierrez", "email": "s@x", "groups": ["CEO", "Tech"]}
    user = SimpleNamespace(role_commercial="asesor", team_id=1)
    with patch.object(auth, "get_nc_user_info", AsyncMock(return_value=nc)), patch.object(
        auth, "sync_user_from_nextcloud", AsyncMock(return_value=user)
    ):
        r = TestClient(app).get("/auth/me", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["groups"] == ["CEO", "Tech"]
