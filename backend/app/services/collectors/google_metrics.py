"""
Google Places metrics collector (Phase C4)

Given a google_place_id, fetch:
- rating
- user_ratings_total (review count)

If GOOGLE_PLACES_API_KEY is not set, collector is disabled and returns None.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import requests

from app.core.config import settings


@dataclass(frozen=True)
class GoogleMetrics:
    rating: Optional[float]
    review_count: Optional[int]


class GoogleCollectorDisabled(Exception):
    pass


def fetch_google_metrics_by_place_id(place_id: str, *, timeout_s: int = 10) -> Optional[GoogleMetrics]:
    api_key = getattr(settings, "GOOGLE_PLACES_API_KEY", None)
    if not api_key:
        print("GOOGLE collector disabled: GOOGLE_PLACES_API_KEY missing")
        return None

    fields = getattr(settings, "GOOGLE_PLACES_FIELDS", "rating,user_ratings_total")

    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "fields": fields,
        "key": api_key,
    }

    print("GOOGLE METRICS REQUEST:", params)

    try:
        r = requests.get(url, params=params, timeout=timeout_s)
        print("GOOGLE METRICS HTTP STATUS:", r.status_code)
        r.raise_for_status()
    except requests.RequestException as e:
        print("GOOGLE METRICS REQUEST FAILED:", e)
        return None

    try:
        data = r.json()
    except ValueError as e:
        print("GOOGLE METRICS JSON PARSE FAILED:", e)
        print("GOOGLE METRICS RAW RESPONSE:", r.text)
        return None

    print("GOOGLE METRICS RESPONSE:", data)

    status = data.get("status")
    if status != "OK":
        print("GOOGLE METRICS NON-OK STATUS:", status)
        if data.get("error_message"):
            print("GOOGLE METRICS ERROR MESSAGE:", data.get("error_message"))
        return None

    result = data.get("result") or {}
    rating = result.get("rating")
    review_count = result.get("user_ratings_total")

    try:
        rating_f = float(rating) if rating is not None else None
    except (TypeError, ValueError):
        rating_f = None

    try:
        review_i = int(review_count) if review_count is not None else None
    except (TypeError, ValueError):
        review_i = None

    print("GOOGLE METRICS PARSED:", rating_f, review_i)

    return GoogleMetrics(rating=rating_f, review_count=review_i)