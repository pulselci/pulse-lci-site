from __future__ import annotations

import os
import time
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from app.core.db import get_conn

logger = logging.getLogger(__name__)

GOOGLE_PLACES_BASE_URL = "https://places.googleapis.com/v1/places"


@dataclass
class GoogleReviewRecord:
    business_id: str
    competitor_id: str
    google_place_id: str
    review_id: str
    author_name: Optional[str]
    author_uri: Optional[str]
    author_photo_uri: Optional[str]
    rating: float
    review_text: Optional[str]
    original_review_text: Optional[str]
    review_language_code: Optional[str]
    original_language_code: Optional[str]
    published_at: Optional[datetime]
    relative_publish_time_description: Optional[str]
    google_maps_uri: Optional[str]
    flag_content_uri: Optional[str]
    visit_date: Optional[str]
    owner_response_text: Optional[str]
    owner_response_published_at: Optional[datetime]
    raw_json: Dict[str, Any]


def _parse_google_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None

    # Handles RFC3339 / ISO-ish strings from Google
    try:
        if value.endswith("Z"):
            value = value.replace("Z", "+00:00")
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _extract_review_text(review: Dict[str, Any]) -> Optional[str]:
    text_obj = review.get("text") or {}
    return text_obj.get("text")


def _extract_original_review_text(review: Dict[str, Any]) -> Optional[str]:
    text_obj = review.get("originalText") or {}
    return text_obj.get("text")


def _extract_review_language_code(review: Dict[str, Any]) -> Optional[str]:
    text_obj = review.get("text") or {}
    return text_obj.get("languageCode")


def _extract_original_language_code(review: Dict[str, Any]) -> Optional[str]:
    text_obj = review.get("originalText") or {}
    return text_obj.get("languageCode")


def map_google_review(
    *,
    business_id: str,
    competitor_id: str,
    google_place_id: str,
    review: Dict[str, Any],
) -> GoogleReviewRecord:
    author = review.get("authorAttribution") or {}

    # Google review resource names are like:
    # places/{place_id}/reviews/{review}
    review_id = review.get("name")
    if not review_id:
        raise ValueError("Google review missing review name / review_id")

    # Owner response fields may not exist yet in your payload.
    owner_response = review.get("ownerResponse") or {}

    return GoogleReviewRecord(
        business_id=business_id,
        competitor_id=competitor_id,
        google_place_id=google_place_id,
        review_id=review_id,
        author_name=author.get("displayName"),
        author_uri=author.get("uri"),
        author_photo_uri=author.get("photoUri"),
        rating=float(review.get("rating", 0)),
        review_text=_extract_review_text(review),
        original_review_text=_extract_original_review_text(review),
        review_language_code=_extract_review_language_code(review),
        original_language_code=_extract_original_language_code(review),
        published_at=_parse_google_timestamp(review.get("publishTime")),
        relative_publish_time_description=review.get("relativePublishTimeDescription"),
        google_maps_uri=review.get("googleMapsUri"),
        flag_content_uri=review.get("flagContentUri"),
        visit_date=review.get("visitDate"),
        owner_response_text=owner_response.get("text"),
        owner_response_published_at=_parse_google_timestamp(owner_response.get("publishTime")),
        raw_json=review,
    )


def fetch_google_place_reviews(
    google_place_id: str,
    *,
    api_key: Optional[str] = None,
    language_code: str = "en",
    sleep_seconds: float = 0.0,
) -> List[Dict[str, Any]]:
    """
    Minimal review fetch using Google Place Details (New).

    NOTE:
    - This requests only the fields we need.
    - Google requires a FieldMask on Place Details.
    - This is intentionally minimal for Step 1.
    """
    from app.core.config import settings

    api_key = api_key or settings.GOOGLE_PLACES_API_KEY

    if not api_key:
        raise RuntimeError("GOOGLE_PLACES_API_KEY is not set")

    url = f"{GOOGLE_PLACES_BASE_URL}/{google_place_id}"

    headers = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": ",".join(
            [
                "id",
                "reviews",
            ]
        ),
    }

    params = {
        "languageCode": language_code,
    }

    if sleep_seconds > 0:
        time.sleep(sleep_seconds)

    response = requests.get(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()

    payload = response.json()
    reviews = payload.get("reviews") or []

    if not isinstance(reviews, list):
        return []

    return reviews

def upsert_google_reviews(records: List[GoogleReviewRecord]) -> int:
    if not records:
        return 0

    sql = """
    insert into public.google_reviews (
        business_id,
        competitor_id,
        source,
        google_place_id,
        review_id,
        author_name,
        author_uri,
        author_photo_uri,
        rating,
        review_text,
        original_review_text,
        review_language_code,
        original_language_code,
        published_at,
        relative_publish_time_description,
        google_maps_uri,
        flag_content_uri,
        visit_date,
        owner_response_text,
        owner_response_published_at,
        raw_json,
        first_seen_at,
        last_seen_at
    )
    values (
        %(business_id)s,
        %(competitor_id)s,
        'google_places',
        %(google_place_id)s,
        %(review_id)s,
        %(author_name)s,
        %(author_uri)s,
        %(author_photo_uri)s,
        %(rating)s,
        %(review_text)s,
        %(original_review_text)s,
        %(review_language_code)s,
        %(original_language_code)s,
        %(published_at)s,
        %(relative_publish_time_description)s,
        %(google_maps_uri)s,
        %(flag_content_uri)s,
        %(visit_date)s,
        %(owner_response_text)s,
        %(owner_response_published_at)s,
        %(raw_json)s::jsonb,
        now(),
        now()
    )
    on conflict (review_id)
    do update set
        business_id = excluded.business_id,
        competitor_id = excluded.competitor_id,
        google_place_id = excluded.google_place_id,
        author_name = excluded.author_name,
        author_uri = excluded.author_uri,
        author_photo_uri = excluded.author_photo_uri,
        rating = excluded.rating,
        review_text = excluded.review_text,
        original_review_text = excluded.original_review_text,
        review_language_code = excluded.review_language_code,
        original_language_code = excluded.original_language_code,
        published_at = excluded.published_at,
        relative_publish_time_description = excluded.relative_publish_time_description,
        google_maps_uri = excluded.google_maps_uri,
        flag_content_uri = excluded.flag_content_uri,
        visit_date = excluded.visit_date,
        owner_response_text = excluded.owner_response_text,
        owner_response_published_at = excluded.owner_response_published_at,
        raw_json = excluded.raw_json,
        last_seen_at = now()
    """

    rows = []
    for r in records:
        rows.append(
            {
                "business_id": r.business_id,
                "competitor_id": r.competitor_id,
                "google_place_id": r.google_place_id,
                "review_id": r.review_id,
                "author_name": r.author_name,
                "author_uri": r.author_uri,
                "author_photo_uri": r.author_photo_uri,
                "rating": r.rating,
                "review_text": r.review_text,
                "original_review_text": r.original_review_text,
                "review_language_code": r.review_language_code,
                "original_language_code": r.original_language_code,
                "published_at": r.published_at,
                "relative_publish_time_description": r.relative_publish_time_description,
                "google_maps_uri": r.google_maps_uri,
                "flag_content_uri": r.flag_content_uri,
                "visit_date": r.visit_date,
                "owner_response_text": r.owner_response_text,
                "owner_response_published_at": r.owner_response_published_at,
                "raw_json": __import__("json").dumps(r.raw_json),
            }
        )

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
        conn.commit()

    return len(rows)


def update_review_sync_state(
    *,
    competitor_id: str,
    business_id: str,
    google_place_id: str,
    last_review_count_seen: Optional[int],
    last_error: Optional[str] = None,
    succeeded: bool = False,
) -> None:
    sql = """
    insert into public.google_review_sync_state (
        competitor_id,
        business_id,
        google_place_id,
        last_attempted_at,
        last_succeeded_at,
        last_error,
        last_review_count_seen
    )
    values (
        %(competitor_id)s,
        %(business_id)s,
        %(google_place_id)s,
        now(),
        case when %(succeeded)s then now() else null end,
        %(last_error)s,
        %(last_review_count_seen)s
    )
    on conflict (competitor_id)
    do update set
        business_id = excluded.business_id,
        google_place_id = excluded.google_place_id,
        last_attempted_at = now(),
        last_succeeded_at = case when %(succeeded)s then now() else public.google_review_sync_state.last_succeeded_at end,
        last_error = excluded.last_error,
        last_review_count_seen = excluded.last_review_count_seen,
        updated_at = now()
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                {
                    "competitor_id": competitor_id,
                    "business_id": business_id,
                    "google_place_id": google_place_id,
                    "last_review_count_seen": last_review_count_seen,
                    "last_error": last_error,
                    "succeeded": succeeded,
                },
            )
        conn.commit()


def ingest_google_reviews_for_competitor(
    *,
    business_id: str,
    competitor_id: str,
    google_place_id: str,
    api_key: Optional[str] = None,
    language_code: str = "en",
) -> Dict[str, Any]:
    """
    Minimal idempotent ingestion entrypoint for Step 1.

    Returns counts only.
    """
    try:
        raw_reviews = fetch_google_place_reviews(
            google_place_id=google_place_id,
            api_key=api_key,
            language_code=language_code,
        )

        records = [
            map_google_review(
                business_id=business_id,
                competitor_id=competitor_id,
                google_place_id=google_place_id,
                review=review,
            )
            for review in raw_reviews
            if review.get("name")
        ]

        upserted = upsert_google_reviews(records)

        update_review_sync_state(
            competitor_id=competitor_id,
            business_id=business_id,
            google_place_id=google_place_id,
            last_review_count_seen=len(raw_reviews),
            last_error=None,
            succeeded=True,
        )

        return {
            "ok": True,
            "business_id": business_id,
            "competitor_id": competitor_id,
            "google_place_id": google_place_id,
            "fetched": len(raw_reviews),
            "upserted": upserted,
        }

    except Exception as exc:
        logger.exception("Review ingestion failed for competitor_id=%s", competitor_id)

        update_review_sync_state(
            competitor_id=competitor_id,
            business_id=business_id,
            google_place_id=google_place_id,
            last_review_count_seen=None,
            last_error=str(exc),
            succeeded=False,
        )

        return {
            "ok": False,
            "business_id": business_id,
            "competitor_id": competitor_id,
            "google_place_id": google_place_id,
            "error": str(exc),
        }