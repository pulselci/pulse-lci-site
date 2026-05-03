from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.report_schedule_service import (
    get_schedule_for_business,
    upsert_schedule_for_business,
)
from app.services.report_periods import Schedule, compute_next_run_at

router = APIRouter(tags=["report_schedules"])


class ReportScheduleUpsertIn(BaseModel):
    frequency: str = Field(..., pattern="^(weekly|monthly)$")
    day_of_week: int | None = Field(None, ge=0, le=6)
    day_of_month: int | None = Field(None, ge=1, le=31)
    hour: int = Field(9, ge=0, le=23)
    minute: int = Field(0, ge=0, le=59)
    timezone: str = "America/New_York"
    is_enabled: bool = True


@router.get("/business/{business_id}/report-schedule")
def get_report_schedule(business_id: UUID):
    row = get_schedule_for_business(business_id)
    if not row:
        raise HTTPException(
            status_code=404,
            detail="No report schedule for this business.",
        )
    return row


@router.put("/business/{business_id}/report-schedule")
def put_report_schedule(business_id: UUID, payload: ReportScheduleUpsertIn):
    # Validation
    if payload.frequency == "weekly" and payload.day_of_week is None:
        raise HTTPException(
            status_code=400,
            detail="day_of_week is required for weekly schedules.",
        )
    if payload.frequency == "monthly" and payload.day_of_month is None:
        raise HTTPException(
            status_code=400,
            detail="day_of_month is required for monthly schedules.",
        )

    sched = Schedule(
        frequency=payload.frequency,
        day_of_week=payload.day_of_week,
        day_of_month=payload.day_of_month,
        hour=payload.hour,
        minute=payload.minute,
        timezone=payload.timezone,
    )

    next_run_at = compute_next_run_at(sched)

    row = upsert_schedule_for_business(
        business_id,
        frequency=payload.frequency,
        day_of_week=payload.day_of_week,
        day_of_month=payload.day_of_month,
        hour=payload.hour,
        minute=payload.minute,
        timezone=payload.timezone,
        is_enabled=payload.is_enabled,
        next_run_at=next_run_at,
    )

    return row
