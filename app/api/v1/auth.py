import httpx
from fastapi import APIRouter, HTTPException, Header, Depends
from typing import Annotated
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.core.config import NC_URL, OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET, OAUTH_CLIENTS
from app.core.security import get_nc_user_info
from app.services.nextcloud_svc import sync_user_from_nextcloud
from app.schemas.user_schemas import OAuthCallback
from app.api.dependencies import get_db

router = APIRouter()

class RefreshRequest(BaseModel):
    refresh_token: str
    client_id: str = None  # For multi-client support

@router.post("/callback")
async def oauth_callback(body: OAuthCallback):
    # Support multiple OAuth clients
    client_id = body.client_id or OAUTH_CLIENT_ID
    client_secret = OAUTH_CLIENTS.get(client_id) or OAUTH_CLIENT_SECRET
    
    # Debug logging
    print(f"[DEBUG] OAuth callback - client_id: {client_id}")
    print(f"[DEBUG] Available clients: {list(OAUTH_CLIENTS.keys())}")
    print(f"[DEBUG] Client found: {client_id in OAUTH_CLIENTS}")
    
    if not client_id or not client_secret:
        raise HTTPException(status_code=500, detail="OAuth not configured")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{NC_URL}/index.php/apps/oauth2/api/v1/token",
            data={
                "grant_type": "authorization_code",
                "code": body.code,
                "redirect_uri": body.redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"OAuth token exchange failed: {response.text}",
            )
        payload = response.json()
        return {
            "access_token": payload.get("access_token"),
            "refresh_token": payload.get("refresh_token"),
            "expires_in": payload.get("expires_in", 3600),
            "token_type": payload.get("token_type", "Bearer"),
        }

@router.post("/refresh")
async def oauth_refresh(body: RefreshRequest):
    """Renueva access_token usando refresh_token. No requiere Authorization header."""
    # Support multiple OAuth clients
    client_id = body.client_id or OAUTH_CLIENT_ID
    client_secret = OAUTH_CLIENTS.get(client_id) or OAUTH_CLIENT_SECRET
    
    if not client_id or not client_secret:
        raise HTTPException(status_code=500, detail="OAuth not configured")
    if not body.refresh_token:
        raise HTTPException(status_code=400, detail="refresh_token required")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{NC_URL}/index.php/apps/oauth2/api/v1/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": body.refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        if response.status_code != 200:
            raise HTTPException(
                status_code=401,
                detail=f"Refresh failed: {response.text}",
            )
        payload = response.json()
        return {
            "access_token": payload.get("access_token"),
            "refresh_token": payload.get("refresh_token"),
            "expires_in": payload.get("expires_in", 3600),
            "token_type": payload.get("token_type", "Bearer"),
        }

@router.get("/me")
async def get_me(
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    nc_data = await get_nc_user_info(authorization)
    user = await sync_user_from_nextcloud(db, nc_data, authorization)

    displayname = nc_data.get("displayname", nc_data["id"])
    parts = displayname.split()
    initials = "".join(p[0].upper() for p in parts[:2]) if parts else "U"

    return {
        "id": nc_data["id"],
        "displayname": displayname,
        "email": nc_data.get("email", ""),
        "initials": initials,
        "role": user.role,
        "teamId": user.team_id,
    }