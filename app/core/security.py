import httpx
from fastapi import HTTPException
from app.core.config import NC_URL


async def get_nc_user_info(authorization: str) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{NC_URL}/ocs/v1.php/cloud/user",
            headers={
                "Authorization": authorization,
                "OCS-APIREQUEST": "true",
                "Accept": "application/json",
            },
        )
        if response.status_code != 200:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        return response.json()["ocs"]["data"]


async def get_nc_user_groups(nc_user_id: str, authorization: str) -> list:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{NC_URL}/ocs/v1.php/cloud/users/{nc_user_id}/groups",
            headers={
                "Authorization": authorization,
                "OCS-APIREQUEST": "true",
                "Accept": "application/json",
            },
        )
        if response.status_code == 200:
            return response.json()["ocs"]["data"]["groups"]
        return []