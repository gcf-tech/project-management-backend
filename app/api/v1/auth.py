import httpx
from fastapi import APIRouter, HTTPException, Header, Depends
from typing import Annotated
from sqlalchemy.orm import Session
from app.core.config import NC_URL, OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET
from app.core.security import get_nc_user_info
from app.services.nextcloud_svc import sync_user_from_nextcloud
from app.schemas.user_schemas import OAuthCallback
from app.api.dependencies import get_db

router = APIRouter()


@router.post("/callback")
async def oauth_callback(body: OAuthCallback):
    if not OAUTH_CLIENT_ID or not OAUTH_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="OAuth not configured")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{NC_URL}/index.php/apps/oauth2/api/v1/token",
            data={
                "grant_type": "authorization_code",
                "code": body.code,
                "redirect_uri": body.redirect_uri,
                "client_id": OAUTH_CLIENT_ID,
                "client_secret": OAUTH_CLIENT_SECRET,
            },
        )
        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"OAuth token exchange failed: {response.text}"
            )
        return response.json()


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