from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ReportScheduleBase(BaseModel):
    frequency: str = Field(..., pattern="^(weekly|monthly)$")
    day_of_week: Optional[int] = Field(None, ge=0, le=6)
    day_of_month: Optional[int] = Field(None, ge=1, le=31)

    hour: int = Field(9, ge=0, le=23)
    minute: int = Field(0, ge=0, le=59)
    timezone: str = "America/New_York"
    is_enabled: bool = True


class ReportScheduleCreate(ReportScheduleBase):
    pass


class ReportScheduleOut(ReportScheduleBase):
    id: UUID
    business_id: UUID
    last_run_at: Optional[datetime]
    next_run_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
