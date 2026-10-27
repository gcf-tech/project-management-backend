"""Router de portafolios (consulta interna del equipo comercial).

Ubicar en app/api/v1/portafolios.py y montar en main.py con:
    from app.api.v1 import ... , portafolios
    app.include_router(portafolios.router, prefix="/api/portafolios")
"""
from typing import Annotated, List

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_user
from app.core.config import PORTAFOLIOS_ADMIN_GROUP, PORTFOLIO_SYNC_SECRET
from app.core.security import get_nc_user_groups
from app.db.models import Portafolio, PortafolioRentabilidad, User
from app.schemas.portafolio_schemas import (
    PortafolioCreate,
    PortafolioOut,
    PortafolioUpdate,
    RentabilidadYearOut,   # nuevo
    RentabilidadYearIn,
    RentabilidadIngestIn,
    RentabilidadIngestOut,
)

router = APIRouter()

MESES_VALIDOS = range(1, 13)

async def require_commercial(user: User = Depends(require_user)) -> User:
    """Acceso a la herramienta de portafolios: equipo comercial.

    Gatea por `role_commercial` (campo manual, como en /auth/me): cualquier
    valor no nulo concede acceso de LECTURA. Editar exige además pertenecer al
    grupo de dirección (ver require_portafolios_admin).

    Nota: un admin global de Nextcloud con role_commercial=NULL quedaría fuera.
    Si quieres que los admins siempre entren, añade:  or user.role == "admin".
    """
    if not user.role_commercial:
        raise HTTPException(status_code=403, detail="Solo para el equipo comercial")
    return user


async def require_portafolios_admin(
    authorization: Annotated[str, Header()],
    user: User = Depends(require_commercial),
) -> User:
    """Edición de portafolios y rentabilidad: solo el grupo de administración.

    Además de ser del equipo comercial, el usuario debe pertenecer al grupo de
    Nextcloud PORTAFOLIOS_ADMIN_GROUP (por defecto "CEO"). Los grupos se leen
    en cada escritura, así que quitar a alguien del grupo surte efecto al momento.
    """
    groups = await get_nc_user_groups(user.nc_user_id, authorization)
    if PORTAFOLIOS_ADMIN_GROUP not in groups:
        raise HTTPException(
            status_code=403,
            detail="Solo el equipo de dirección puede modificar portafolios",
        )
    return user


def require_sync_secret(
    x_sync_secret: Annotated[str | None, Header()] = None,
) -> None:
    """Auth máquina para el webhook de rentabilidad (no usa Nextcloud).

    La feature se activa por configuración: si PORTFOLIO_SYNC_SECRET no está
    seteado (la mayoría de los deployments), el endpoint rechaza todo. Solo la
    company que configure el secreto (p.ej. Capital) puede ingestar."""
    if not PORTFOLIO_SYNC_SECRET or x_sync_secret != PORTFOLIO_SYNC_SECRET:
        raise HTTPException(status_code=401, detail="Invalid sync secret")


@router.get("", response_model=List[PortafolioOut])
def list_portafolios(
    incluir_inactivos: bool = Query(False, description="Incluir portafolios con activo=False"),
    _user: User = Depends(require_commercial),
    db: Session = Depends(get_db),
):
    q = db.query(Portafolio)
    if not incluir_inactivos:
        q = q.filter(Portafolio.activo.is_(True))
    return q.order_by(Portafolio.orden.asc(), Portafolio.nombre.asc()).all()


@router.get("/{portafolio_id}", response_model=PortafolioOut)
def get_portafolio(
    portafolio_id: int,
    _user: User = Depends(require_commercial),
    db: Session = Depends(get_db),
):
    p = db.query(Portafolio).filter(Portafolio.id == portafolio_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Portafolio no encontrado")
    return p


@router.post("", response_model=PortafolioOut, status_code=201)
def create_portafolio(
    body: PortafolioCreate,
    _user: User = Depends(require_portafolios_admin),
    db: Session = Depends(get_db),
):
    if db.query(Portafolio).filter(Portafolio.nombre == body.nombre).first():
        raise HTTPException(status_code=409, detail="Ya existe un portafolio con ese nombre")
    p = Portafolio(**body.model_dump())
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@router.patch("/{portafolio_id}", response_model=PortafolioOut)
def update_portafolio(
    portafolio_id: int,
    body: PortafolioUpdate,
    _user: User = Depends(require_portafolios_admin),
    db: Session = Depends(get_db),
):
    p = db.query(Portafolio).filter(Portafolio.id == portafolio_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Portafolio no encontrado")

    cambios = body.model_dump(exclude_unset=True)
    if "nombre" in cambios and cambios["nombre"] != p.nombre:
        if db.query(Portafolio).filter(Portafolio.nombre == cambios["nombre"]).first():
            raise HTTPException(status_code=409, detail="Ya existe un portafolio con ese nombre")

    for campo, valor in cambios.items():
        setattr(p, campo, valor)
    db.commit()          # updated_at se refresca solo (onupdate=utc_now)
    db.refresh(p)
    return p


@router.delete("/{portafolio_id}", status_code=204)
def delete_portafolio(
    portafolio_id: int,
    _user: User = Depends(require_portafolios_admin),
    db: Session = Depends(get_db),
):
    """Borrado definitivo. Para ocultar sin borrar, usa PATCH con activo=False."""
    p = db.query(Portafolio).filter(Portafolio.id == portafolio_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Portafolio no encontrado")
    db.delete(p)
    db.commit()

@router.get("/{portafolio_id}/rentabilidad", response_model=List[RentabilidadYearOut])
def get_rentabilidad(
    portafolio_id: int,
    _user: User = Depends(require_commercial),
    db: Session = Depends(get_db),
):
    if not db.query(Portafolio).filter(Portafolio.id == portafolio_id).first():
        raise HTTPException(status_code=404, detail="Portafolio no encontrado")
 
    filas = (
        db.query(PortafolioRentabilidad)
        .filter(PortafolioRentabilidad.portafolio_id == portafolio_id)
        .order_by(PortafolioRentabilidad.anio.asc(), PortafolioRentabilidad.mes.asc())
        .all()
    )
    por_anio: dict[int, dict[int, object]] = {}
    for f in filas:
        por_anio.setdefault(f.anio, {})[f.mes] = f.valor
    return [RentabilidadYearOut(anio=a, meses=por_anio[a]) for a in sorted(por_anio)]
 
 
@router.put("/{portafolio_id}/rentabilidad/{anio}", response_model=RentabilidadYearOut)
def guardar_anio(
    portafolio_id: int,
    anio: int,
    body: RentabilidadYearIn,
    _user: User = Depends(require_portafolios_admin),
    db: Session = Depends(get_db),
):
    if not db.query(Portafolio).filter(Portafolio.id == portafolio_id).first():
        raise HTTPException(status_code=404, detail="Portafolio no encontrado")
 
    # Filas actuales del año, indexadas por mes.
    existentes = {
        f.mes: f
        for f in db.query(PortafolioRentabilidad)
        .filter(
            PortafolioRentabilidad.portafolio_id == portafolio_id,
            PortafolioRentabilidad.anio == anio,
        )
        .all()
    }
 
    # PUT = reemplazo total del año: mes con valor → upsert; null/ausente → borrar.
    for mes in MESES_VALIDOS:
        valor = body.meses.get(mes)
        fila = existentes.get(mes)
        if valor is None:
            if fila is not None:
                db.delete(fila)
        elif fila is not None:
            fila.valor = valor
        else:
            db.add(
                PortafolioRentabilidad(
                    portafolio_id=portafolio_id, anio=anio, mes=mes, valor=valor
                )
            )
 
    db.commit()
 
    filas = (
        db.query(PortafolioRentabilidad)
        .filter(
            PortafolioRentabilidad.portafolio_id == portafolio_id,
            PortafolioRentabilidad.anio == anio,
        )
        .order_by(PortafolioRentabilidad.mes.asc())
        .all()
    )
    return RentabilidadYearOut(anio=anio, meses={f.mes: f.valor for f in filas})
 
 
@router.delete("/{portafolio_id}/rentabilidad/{anio}", status_code=204)
def borrar_anio(
    portafolio_id: int,
    anio: int,
    _user: User = Depends(require_portafolios_admin),
    db: Session = Depends(get_db),
):
    db.query(PortafolioRentabilidad).filter(
        PortafolioRentabilidad.portafolio_id == portafolio_id,
        PortafolioRentabilidad.anio == anio,
    ).delete(synchronize_session=False)
    db.commit()


@router.post("/rentabilidad/ingest", response_model=RentabilidadIngestOut)
def ingest_rentabilidad(
    body: RentabilidadIngestIn,
    _ok: None = Depends(require_sync_secret),
    db: Session = Depends(get_db),
):
    """Ingesta máquina de la rentabilidad mensual desde la plataforma de
    inversión (webhook del edge function `reports`). Mapea cada item al
    portafolio por nombre (case-insensitive) y hace upsert de la celda
    (portafolio, año, mes). Idempotente: reenviar el mismo mes no duplica.
    Los nombres no encontrados se ignoran (van en `skipped`), no fallan."""
    upserted: list[str] = []
    skipped: list[str] = []
    for item in body.items:
        p = (
            db.query(Portafolio)
            .filter(Portafolio.nombre.ilike(item.portafolio.strip()))
            .first()
        )
        if not p:
            skipped.append(item.portafolio)
            continue
        fila = (
            db.query(PortafolioRentabilidad)
            .filter(
                PortafolioRentabilidad.portafolio_id == p.id,
                PortafolioRentabilidad.anio == body.anio,
                PortafolioRentabilidad.mes == body.mes,
            )
            .first()
        )
        if fila is not None:
            fila.valor = item.valor
        else:
            db.add(
                PortafolioRentabilidad(
                    portafolio_id=p.id, anio=body.anio, mes=body.mes, valor=item.valor
                )
            )
        upserted.append(p.nombre)
    db.commit()
    return RentabilidadIngestOut(
        anio=body.anio, mes=body.mes, upserted=upserted, skipped=skipped
    )