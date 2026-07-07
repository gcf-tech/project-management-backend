import httpx
from datetime import datetime, date
from typing import Optional
from sqlalchemy.orm import Session
from app.core.config import NC_URL
from app.core.security import get_nc_user_info, get_nc_user_groups
from app.db.models import User, Team


def parse_date(date_str: Optional[str]) -> Optional[date]:
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str.replace("Z", "")).date()
    except:
        return None


async def sync_user_from_nextcloud(db: Session, nc_data: dict, authorization: str) -> User:
    groups = await get_nc_user_groups(nc_data["id"], authorization)

    if "admin" in groups:
        role = "admin"
    elif "Supervisors" in groups:
        role = "leader"
    else:
        role = "member"

    excluded = {"admin", "Supervisors", "emploiee"}
    team_groups = [g for g in groups if g not in excluded]

    team = None
    if team_groups:
        team_name = team_groups[0]
        team = db.query(Team).filter(Team.name == team_name).first()
        if not team:
            is_tech = team_name.lower() == "tech"
            team = Team(name=team_name, is_tech_team=is_tech)
            db.add(team)
            db.flush()

    user = db.query(User).filter(User.nc_user_id == nc_data["id"]).first()
    if not user:
        user = User(
            nc_user_id=nc_data["id"],
            display_name=nc_data.get("displayname", nc_data["id"]),
            email=nc_data.get("email"),
            role=role,
            role_commercial=None,  # Manual field, not synced from Nextcloud
            # team_id sólo se siembra en la CREACIÓN del usuario (default inicial
            # desde el grupo de Nextcloud). En logins posteriores NO se toca.
            team_id=team.id if team else None,
        )
        db.add(user)
    else:
        user.display_name = nc_data.get("displayname", nc_data["id"])
        user.email = nc_data.get("email")
        user.role = role
        # role_commercial y team_id NO se actualizan: son manuales en la BD.
        # (team_id se dejó de sincronizar desde los grupos de Nextcloud para que
        #  las asignaciones manuales de equipo no se reviertan en cada login.)

    db.commit()
    db.refresh(user)
    return user


async def fetch_deck_boards(authorization: str) -> list:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{NC_URL}/index.php/apps/deck/api/v1.0/boards",
            headers={
                "Authorization": authorization,
                "OCS-APIREQUEST": "true",
                "Accept": "application/json",
            },
        )
        if response.status_code == 401:
            raise httpx.HTTPStatusError("Unauthorized", request=response.request, response=response)
        if response.status_code != 200:
            return []
        boards = response.json()
        if not isinstance(boards, list):
            return []
        return [
            {"id": b.get("id"), "title": b.get("title", "Untitled")}
            for b in boards
            if not b.get("archived", False) and b.get("id") is not None
        ]


async def fetch_deck_cards(board_id: int, authorization: str) -> list:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{NC_URL}/index.php/apps/deck/api/v1.0/boards/{board_id}/stacks",
            headers={
                "Authorization": authorization,
                "OCS-APIREQUEST": "true",
                "Accept": "application/json",
            },
        )
        if response.status_code == 401:
            raise httpx.HTTPStatusError("Unauthorized", request=response.request, response=response)
        if response.status_code in [403, 404]:
            return []
        if response.status_code != 200:
            return []

        stacks = response.json()
        if not isinstance(stacks, list):
            return []
        cards = []
        for stack in stacks:
            for card in stack.get("cards") or []:
                card_id = card.get("id")
                if card_id is None:
                    continue
                cards.append({
                    "id": card_id,
                    "title": card.get("title", "Untitled"),
                    "description": card.get("description", ""),
                    "duedate": card.get("duedate"),
                    "labels": [
                        lbl.get("title", "")
                        for lbl in (card.get("labels") or [])
                    ],
                    "stack": stack.get("title", ""),
                })
        return cards

async def push_nc_notification(authorization: str, nc_user_id: str, subject: str, message: str = "") -> bool:
    """Best-effort mirror of a Deck notification into the Nextcloud notifications
    app (bell icon). Uses the admin_notifications API, which requires the calling
    token to belong to a Nextcloud admin; on any failure we silently return False
    so the in-app notification remains the source of truth.
    """
    url = f"{NC_URL}/ocs/v2.php/apps/notifications/api/v2/admin_notifications/{nc_user_id}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": authorization,
                    "OCS-APIREQUEST": "true",
                    "Accept": "application/json",
                },
                data={"shortMessage": subject[:255], "longMessage": message[:4000]},
            )
            return resp.status_code in (200, 201)
    except Exception:
        return False
