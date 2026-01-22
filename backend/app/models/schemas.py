from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, conint, confloat


# -------------------------
# Business + Competitors
# -------------------------

class CompetitorIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    website_url: Optional[HttpUrl] = None
    google_place_id: Optional[str] = Field(default=None, max_length=200)
    google_maps_url: Optional[HttpUrl] = None


class BusinessIntakeIn(BaseModel):
    business_name: str = Field(min_length=1, max_length=200)
    primary_domain: Optional[str] = Field(default=None, max_length=255)
    city: Optional[str] = Field(default=None, max_length=120)
    state: Optional[str] = Field(default=None, max_length=120)
    country: Optional[str] = Field(default="US", max_length=2)
    notes: Optional[str] = Field(default=None, max_length=2000)
    competitors: list[CompetitorIn] = Field(default_factory=list)


class BusinessOut(BaseModel):
    id: UUID
    name: str
    primary_domain: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime


class CompetitorOut(BaseModel):
    id: UUID
    business_id: UUID
    name: str
    website_url: Optional[str] = None
    google_place_id: Optional[str] = None
    google_maps_url: Optional[str] = None
    created_at: datetime


class BusinessWithCompetitorsOut(BaseModel):
    business: BusinessOut
    competitors: list[CompetitorOut]


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

    raw: Optional[dict] = None


class SnapshotBulkIn(BaseModel):
    snapshots: list[SnapshotIn] = Field(min_length=1, max_length=500)


class SnapshotBulkOut(BaseModel):
    inserted: int
    skipped_duplicates: int

from typing import Optional
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel

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
# Reports
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


class ReportLatestOut(BaseModel):
    report: Optional[ReportOut] = None
    signed_url_placeholder: Optional[str] = None
