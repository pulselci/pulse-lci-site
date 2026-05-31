from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, conint, confloat


# -------------------------
# Business + Competitors
# -------------------------

class CompetitorIn(BaseModel):
    name: str
    website_url: str | None = None
    google_place_id: str | None = None
    google_maps_url: str | None = None
    is_business: bool = False


class BusinessIntakeIn(BaseModel):
    business_name: str = Field(min_length=1, max_length=200)
    primary_domain: Optional[str] = Field(default=None, max_length=255)
    city: Optional[str] = Field(default=None, max_length=120)
    state: Optional[str] = Field(default=None, max_length=120)
    country: Optional[str] = Field(default="US", max_length=2)
    notes: Optional[str] = Field(default=None, max_length=2000)
    customer_label: Optional[str] = Field(default="customers", max_length=50)
    competitors: list[CompetitorIn] = Field(default_factory=list)


class BusinessOut(BaseModel):
    id: UUID
    name: str
    primary_domain: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    notes: Optional[str] = None
    customer_label: Optional[str] = "customers"
    created_at: datetime


class CompetitorOut(BaseModel):
    id: UUID
    business_id: UUID
    name: str
    website_url: str | None = None
    google_place_id: str | None = None
    google_maps_url: str | None = None
    created_at: datetime
    is_business: bool = False


class BusinessWithCompetitorsOut(BaseModel):
    business: BusinessOut
    competitors: list[CompetitorOut]


class ReportRecipientIn(BaseModel):
    email: EmailStr


class ReportRecipientOut(BaseModel):
    id: UUID
    business_id: UUID
    email: EmailStr
    is_enabled: bool = True
    created_at: datetime


class BusinessSeedIn(BaseModel):
    business_name: str = Field(min_length=1, max_length=200)
    primary_domain: Optional[str] = Field(default=None, max_length=255)
    city: Optional[str] = Field(default=None, max_length=120)
    state: Optional[str] = Field(default=None, max_length=120)
    country: Optional[str] = Field(default="US", max_length=2)
    notes: Optional[str] = Field(default=None, max_length=2000)
    customer_label: Optional[str] = Field(default="customers", max_length=50)
    google_place_id: Optional[str] = None
    google_maps_url: Optional[str] = None
    website_url: Optional[str] = None


class OnboardingIn(BaseModel):
    business: BusinessSeedIn
    competitors: list[CompetitorIn] = Field(default_factory=list)
    recipient_emails: list[EmailStr] = Field(default_factory=list)
    auto_generate_first_report: bool = False
    send_first_report: bool = False
    run_initial_collection: bool = False
    billing_mode: str = "free_preview"
    send_checkout_email: bool = False
    schedule_hour: int = Field(9, ge=0, le=23)
    schedule_minute: int = Field(0, ge=0, le=59)
    schedule_timezone: str = "America/New_York"


class OnboardingOut(BaseModel):
    business: BusinessOut
    competitors: list[CompetitorOut]
    recipients: list[ReportRecipientOut]
    schedule: dict[str, Any]
    first_report: Optional[dict[str, Any]] = None
    checkout_url: Optional[str] = None
    checkout_session_id: Optional[str] = None
    checkout_plan: Optional[str] = None


# -------------------------
# Snapshots
# -------------------------

class SnapshotIn(BaseModel):
    business_id: UUID
    competitor_id: UUID
    observed_at: Optional[datetime] = None

    google_rating: Optional[confloat(ge=0, le=5)] = None
    google_review_count: Optional[conint(ge=0)] = None

    offer_summary: Optional[str] = Field(default=None, max_length=2000)
    price_hint: Optional[str] = Field(default=None, max_length=200)
    visibility_score: Optional[confloat(ge=0)] = None
    notes: Optional[str] = Field(default=None, max_length=4000)

    raw: Optional[dict[str, Any]] = None


class SnapshotDetailOut(BaseModel):
    id: UUID
    business_id: UUID
    competitor_id: Optional[UUID] = None
    observed_at: Optional[datetime] = None
    created_at: datetime

    google_rating: Optional[float] = None
    google_review_count: Optional[int] = None
    offer_summary: Optional[str] = None
    price_hint: Optional[str] = None
    visibility_score: Optional[int] = None
    notes: Optional[str] = None

    raw: Optional[dict[str, Any]] = None


class SnapshotBulkIn(BaseModel):
    snapshots: list[SnapshotIn] = Field(min_length=1, max_length=500)


class SnapshotBulkOut(BaseModel):
    inserted: int
    skipped_duplicates: int


class SnapshotListItemOut(BaseModel):
    id: UUID
    business_id: UUID
    competitor_id: UUID
    competitor_name: str
    created_at: datetime
    google_rating: Optional[float] = None
    google_review_count: Optional[int] = None
    observed_at: datetime


# -------------------------
# Reports (legacy “reports” table)
# -------------------------

class ReportRegisterIn(BaseModel):
    business_id: UUID
    period_start: date
    period_end: date
    title: str = Field(min_length=1, max_length=250)
    summary: Optional[str] = Field(default=None, max_length=4000)

    storage_bucket: Optional[str] = Field(default=None, max_length=100)
    storage_path: Optional[str] = Field(default=None, max_length=500)


class ReportOut(BaseModel):
    id: UUID
    business_id: UUID
    period_start: date
    period_end: date
    title: str
    status: str
    storage_bucket: Optional[str] = None
    storage_path: Optional[str] = None
    summary: Optional[str] = None
    created_at: datetime


# -------------------------
# Generated Reports (generated_reports table)
# -------------------------

class GeneratedReportOut(BaseModel):
    id: UUID
    business_id: UUID
    period_start: datetime
    period_end: datetime
    schedule_id: Optional[UUID] = None
    generated_at: Optional[datetime] = None
    status: str
    title: str
    summary_text: Optional[str] = None
    sections: dict[str, Any] = Field(default_factory=dict)
    inputs: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    created_at: Optional[datetime] = None


class ReportLatestOut(BaseModel):
    report: Optional[GeneratedReportOut] = None
    signed_url_placeholder: Optional[str] = None