from fastapi import APIRouter, Response
from app.core.config import BUSINESS_TIMEZONE, BUSINESS_HOUR_START, BUSINESS_HOUR_END

router = APIRouter()


@router.get("/business-hours")
async def get_business_hours(response: Response):
    # Business hours are tenant-wide config that rarely changes — let any
    # intermediary (browser, CDN, reverse-proxy) cache for 1 hour.
    response.headers["Cache-Control"] = "public, max-age=3600"
    return {
        "timezone":   BUSINESS_TIMEZONE,
        "start_hour": BUSINESS_HOUR_START,
        "end_hour":   BUSINESS_HOUR_END,
    }
