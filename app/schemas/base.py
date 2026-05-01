from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.core.datetime_utils import to_rfc3339_z


class UTCModel(BaseModel):
    model_config = ConfigDict(
        json_encoders={datetime: to_rfc3339_z},
        arbitrary_types_allowed=True,
    )
