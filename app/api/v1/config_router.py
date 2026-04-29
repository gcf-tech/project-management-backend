from fastapi import APIRouter
from app.core.config import BUSINESS_TIMEZONE, BUSINESS_HOUR_START, BUSINESS_HOUR_END

router = APIRouter()


@router.get("/business-hours")
async def get_business_hours():
    return {
        "timezone":   BUSINESS_TIMEZONE,
        "start_hour": BUSINESS_HOUR_START,
        "end_hour":   BUSINESS_HOUR_END,
    }
