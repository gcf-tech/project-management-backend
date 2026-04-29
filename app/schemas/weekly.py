from __future__ import annotations

from datetime import datetime
from typing import Dict, Any, Literal, Optional
from pydantic import BaseModel, ConfigDict


class WeeklyBlockUnified(BaseModel):
    id: str
    source: Literal["manual", "task", "activity"]
    source_ref_id: Optional[str]
    title: str
    start_at: datetime
    duration_minutes: int
    color: Optional[str]
    metadata: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(from_attributes=True)
