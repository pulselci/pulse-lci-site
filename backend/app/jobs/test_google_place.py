from __future__ import annotations

import sys
import requests

from app.core.config import settings
from app.services.collectors.google_metrics import fetch_google_metrics_by_place_id


def debug_google_response(place_id: str) -> None:
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "fields": getattr(settings, "GOOGLE_PLACES_FIELDS", "rating,user_ratings_total"),
        "key": getattr(settings, "GOOGLE_PLACES_API_KEY", None),
    }
    r = requests.get(url, params=params, timeout=10)
    print("HTTP:", r.status_code)
    try:
        data = r.json()
    except Exception:
        print("Non-JSON response:", r.text[:400])
        return

    print("status:", data.get("status"))
    if data.get("error_message"):
        print("error_message:", data.get("error_message"))
    # Helpful when diagnosing INVALID_REQUEST, etc.
    if data.get("result") is None:
        print("result: None")
    else:
        print("result keys:", list((data.get("result") or {}).keys()))


def main() -> int:
    print("GOOGLE_PLACES_API_KEY set:", bool(getattr(settings, "GOOGLE_PLACES_API_KEY", None)))

    if len(sys.argv) < 2:
        print("Usage: python -m app.jobs.test_google_place <google_place_id>")
        return 2

    place_id = sys.argv[1]

    metrics = fetch_google_metrics_by_place_id(place_id)
    if metrics is None:
        print("No metrics returned from collector. Debugging raw Google response:")
        debug_google_response(place_id)
        return 1

    print(f"rating={metrics.rating} reviews={metrics.review_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
