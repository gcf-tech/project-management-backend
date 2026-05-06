"""Per-user settings API.

Routes (prefix /api/settings):
    GET    /caldav-credential   — metadata only: { configured, set_at }
    POST   /caldav-credential   — validate + store App Password
    DELETE /caldav-credential   — remove credential and invalidate cache
"""
from __future__ import annotations

import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_db
from app.core.cache import get_cache
from app.core.config import CALDAV_AUTH_MODE, CALDAV_TIMEOUT_S, NC_URL
from app.core.crypto import encrypt_secret
from app.core.datetime_utils import utc_now

router = APIRouter()
logger = logging.getLogger(__name__)


class CalDAVCredentialIn(BaseModel):
    app_password: str = Field(min_length=10, max_length=256)


# ── helpers ────────────────────────────────────────────────────────────────

def _auth_header(authorization: str | None):
    """Shared guard: require a valid Authorization header and return the user."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")


async def _get_user(authorization: str | None, db: Session):
    _auth_header(authorization)
    user = await get_current_user(authorization, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user


async def _probe_caldav(nc_user_id: str, app_password: str) -> None:
    """PROPFIND the Nextcloud principal to validate the App Password before storing."""
    probe_url = f"{NC_URL}/remote.php/dav/principals/users/{nc_user_id}/"
    try:
        async with httpx.AsyncClient(timeout=CALDAV_TIMEOUT_S) as client:
            resp = await client.request(
                "PROPFIND",
                probe_url,
                auth=(nc_user_id, app_password),
                headers={"Depth": "0"},
            )
    except Exception as exc:
        logger.warning("[settings] caldav probe network error user=%s: %s", nc_user_id, exc)
        raise HTTPException(
            status_code=502,
            detail="No se pudo contactar el servidor CalDAV. Intente más tarde.",
        ) from exc

    if resp.status_code == 401:
        raise HTTPException(
            status_code=400,
            detail="App Password inválido. Verifica las credenciales en Nextcloud.",
        )
    if resp.status_code not in (200, 207):
        logger.warning(
            "[settings] caldav probe unexpected status=%d user=%s",
            resp.status_code, nc_user_id,
        )
        raise HTTPException(
            status_code=502,
            detail=f"CalDAV respondió HTTP {resp.status_code}. Intente más tarde.",
        )


# ── endpoints ──────────────────────────────────────────────────────────────

@router.get("/caldav-credential")
async def get_caldav_credential_status(
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    user = await _get_user(authorization, db)
    set_at = user.nc_caldav_token_set_at.isoformat() if user.nc_caldav_token_set_at else None
    return {"configured": user.has_caldav_credential(), "set_at": set_at}


@router.post("/caldav-credential")
async def set_caldav_credential(
    body: CalDAVCredentialIn,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if CALDAV_AUTH_MODE != "app_password":
        raise HTTPException(
            status_code=400,
            detail="CalDAV App Password mode is not active on this server.",
        )
    user = await _get_user(authorization, db)

    await _probe_caldav(user.nc_user_id, body.app_password)

    ciphertext, nonce = encrypt_secret(body.app_password)
    user.nc_caldav_token_ciphertext = ciphertext
    user.nc_caldav_token_iv         = nonce
    user.nc_caldav_token_set_at     = utc_now()
    db.commit()

    cache = get_cache()
    await cache.delete_prefix(f"cal:{user.nc_user_id}:")
    logger.info("[settings] caldav credential set user=%s", user.nc_user_id)

    return {"ok": True, "set_at": user.nc_caldav_token_set_at.isoformat()}


@router.delete("/caldav-credential", status_code=204)
async def delete_caldav_credential(
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    user = await _get_user(authorization, db)

    user.nc_caldav_token_ciphertext = None
    user.nc_caldav_token_iv         = None
    user.nc_caldav_token_set_at     = None
    db.commit()

    cache = get_cache()
    await cache.delete_prefix(f"cal:{user.nc_user_id}:")
    logger.info("[settings] caldav credential deleted user=%s", user.nc_user_id)

    return Response(status_code=204)
