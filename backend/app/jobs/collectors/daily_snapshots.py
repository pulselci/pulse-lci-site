"""
Daily snapshot orchestrator (Phase C5)

Wired to:
- list businesses
- load competitors per business
- collect Google rating + review count (if google_place_id exists and API key set)
- reuse insert_snapshots_bulk() (same logic used by the API)

MVP notes:
- If GOOGLE_PLACES_API_KEY is missing, Google collection becomes a no-op.
- Idempotency relies on DB uniqueness + IntegrityError skip in insert_snapshots_bulk.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import List
from uuid import UUID

from app.models.schemas import SnapshotBulkIn, SnapshotIn
from app.services.business_service import get_business_with_competitors, list_businesses
from app.services.collectors.google_metrics import fetch_google_metrics_by_place_id
from app.services.snapshot_service import insert_snapshots_bulk


@dataclass
class JobResult:
    businesses: int = 0
    competitors: int = 0
    inserted: int = 0
    skipped: int = 0
    failed: int = 0


def _observed_at_for_day(observed_date: date) -> datetime:
    # Use midnight UTC for "daily" idempotency.
    return datetime(
        observed_date.year,
        observed_date.month,
        observed_date.day,
        0,
        0,
        0,
        tzinfo=timezone.utc,
    )


def run_daily_snapshot_collection(
    *,
    observed_date: date,
    dry_run: bool,
) -> JobResult:
    result = JobResult()
    observed_at = _observed_at_for_day(observed_date)

    businesses = list_businesses()
    result.businesses = len(businesses)

    total_competitors = 0

    for b in businesses:
        business_id: UUID = b.id
        bwc = get_business_with_competitors(business_id)
        competitors = bwc.competitors or []
        total_competitors += len(competitors)

        snapshots: List[SnapshotIn] = []

        for c in competitors:
            rating = None
            review_count = None

            # Only attempt if we have a place_id; safe no-op if API key missing
            if c.google_place_id:
                metrics = fetch_google_metrics_by_place_id(c.google_place_id)
                if metrics is not None:
                    rating = metrics.rating
                    review_count = metrics.review_count

            snapshots.append(
                SnapshotIn(
                    business_id=business_id,
                    competitor_id=c.id,
                    observed_at=observed_at,
                    google_rating=rating,
                    google_review_count=review_count,
                    offer_summary=None,
                    price_hint=None,
                    visibility_score=None,
                    notes="auto: daily_collector",
                    raw={
                        "source": "daily_collector",
                        "google_place_id": c.google_place_id,
                    },
                )
            )

        if dry_run:
            continue

        try:
            out = insert_snapshots_bulk(SnapshotBulkIn(snapshots=snapshots))
            result.inserted += out.inserted
            result.skipped += out.skipped_duplicates
        except Exception:
            # Keep going so one business failing doesn't kill the whole run
            result.failed += len(snapshots)

    result.competitors = total_competitors
    return result
