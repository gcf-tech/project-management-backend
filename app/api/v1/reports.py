"""Reports API — performance export endpoint."""
from __future__ import annotations

import uuid
from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.core.reports.auth import require_admin_or_lead
from app.core.reports.exceptions import EmptyScopeError, ReportGenerationError
from app.db.models import User
from app.schemas.report_request import ReportRequest
from app.services.reports.metrics_aggregator import MetricsAggregator
from app.services.reports.performance_report_service import PerformanceReportService

router = APIRouter(prefix="/reports", tags=["reports"])

# TODO: apply rate limit 5/minute/user once a rate-limiting library is wired up


@router.post("/performance/export")
async def export_performance_report(
    request: ReportRequest,
    current_user: User = Depends(require_admin_or_lead),
    db: Session = Depends(get_db),
):
    aggregator = MetricsAggregator(db)
    service = PerformanceReportService(aggregator)

    try:
        raw_bytes, meta = service.generate(request, current_user)
    except EmptyScopeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ReportGenerationError as exc:
        request_id = uuid.uuid4()
        raise HTTPException(
            status_code=500,
            detail=f"Report generation failed (request_id={request_id}): {exc}",
        )

    return StreamingResponse(
        BytesIO(raw_bytes),
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": f'attachment; filename="{meta.filename}"',
            "X-Report-Sheets": str(meta.sheet_count),
            "X-Report-Rows": str(meta.row_count),
        },
    )
