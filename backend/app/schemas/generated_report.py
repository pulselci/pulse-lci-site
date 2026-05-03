from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import BaseModel


class GeneratedReportOut(BaseModel):
    id: UUID
    business_id: UUID
    schedule_id: Optional[UUID]

    period_start: datetime
    period_end: datetime
    generated_at: datetime

    status: str
    title: str
    summary_text: str

    sections: Dict[str, Any]
    inputs: Dict[str, Any]
    error: Optional[str]

    class Config:
        from_attributes = True
