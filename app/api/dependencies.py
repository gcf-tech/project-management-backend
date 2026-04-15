from typing import Optional, Annotated
from fastapi import Header, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.database import SessionLocal
from app.db.models import User
from app.core.security import get_nc_user_info
from app.services.nextcloud_svc import sync_user_from_nextcloud


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
) -> Optional[User]:
    if not authorization:
        return None
    try:
        nc_data = await get_nc_user_info(authorization)
        return await sync_user_from_nextcloud(db, nc_data, authorization)
    except HTTPException:
        return None


async def require_user(
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
) -> User:
    try:
        nc_data = await get_nc_user_info(authorization)
        return await sync_user_from_nextcloud(db, nc_data, authorization)
    except HTTPException:
        raise HTTPException(status_code=401, detail="Invalid token")