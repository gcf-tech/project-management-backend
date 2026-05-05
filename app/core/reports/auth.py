from fastapi import Depends, HTTPException
from app.db.models import User
from app.api.dependencies import require_user

# Roles found in app/db/models.py:38 — Enum("member", "leader", "admin")
PRIVILEGED_ROLES = {"admin", "leader"}


async def require_admin_or_lead(
    user: User = Depends(require_user),
) -> User:
    if user.role not in PRIVILEGED_ROLES:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return user
