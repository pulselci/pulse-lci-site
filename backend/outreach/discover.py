"""
Prospect discovery script for Pulse LCI cold outreach.

Usage:
    python -m outreach.discover --city "Dallas" --state "TX"
    python -m outreach.discover --city "Phoenix" --state "AZ" --categories "auto_repair,dentist"

What it does:
1. Searches Google Places for review-heavy local businesses in the given city
2. Filters out chains and low-review-count businesses
3. Finds their top nearby competitor (for personalization)
4. Scrapes their website for a contact email
5. Generates a personalized draft cold email
6. Inserts into outreach_prospects table with status "draft_ready"

Skips businesses already in the DB.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import requests

# Allow running as `python -m outreach.discover` from /backend
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings
from app.core.db import get_conn
from outreach.draft_email import generate_draft

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_CATEGORIES = [
    "auto repair shop",
    "medical spa",
    "dental office",
    "hair salon",
    "gym",
    "chiropractor",
    "physical therapy",
]

# Chains to skip (partial match, lowercase)
CHAIN_BLOCKLIST = [
    "jiffy lube", "midas", "firestone", "pep boys", "meineke",
    "aspen dental", "heartland dental", "pacific dental",
    "great clips", "supercuts", "sport clips",
    "planet fitness", "anytime fitness", "la fitness",
    "massage envy",
]

MIN_REVIEWS = 15       # must have enough reviews to be worth targeting
MAX_REVIEWS = 800      # avoid dominant players (hard to sell to)
MIN_RATING = 3.2       # too low = dying business
MAX_RATING = 4.85      # near-perfect = low urgency

GOOGLE_PLACES_TEXT_SEARCH = "https://maps.googleapis.com/maps/api/place/textsearch/json"
GOOGLE_PLACES_DETAILS = "https://maps.googleapis.com/maps/api/place/details/json"
GOOGLE_PLACES_NEARBY = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"


# ---------------------------------------------------------------------------
# Google Places helpers
# ---------------------------------------------------------------------------

def _api_key() -> str:
    key = getattr(settings, "GOOGLE_PLACES_API_KEY", None)
    if not key:
        raise RuntimeError("GOOGLE_PLACES_API_KEY is not set in .env")
    return key


def search_places(query: str, city: str, state: str) -> list[dict]:
    """Text search for businesses matching query in city, state."""
    location_query = f"{query} in {city}, {state}"
    results = []
    next_page_token = None

    for _ in range(3):  # max 3 pages = 60 results
        params: dict = {"query": location_query, "key": _api_key()}
        if next_page_token:
            params = {"pagetoken": next_page_token, "key": _api_key()}
            time.sleep(2)  # Google requires delay for page tokens

        try:
            r = requests.get(GOOGLE_PLACES_TEXT_SEARCH, params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"  [WARN] Places search failed: {e}")
            break

        results.extend(data.get("results", []))
        next_page_token = data.get("next_page_token")
        if not next_page_token:
            break

    return results


def get_place_details(place_id: str) -> dict:
    """Fetch website and phone for a place."""
    try:
        r = requests.get(
            GOOGLE_PLACES_DETAILS,
            params={
                "place_id": place_id,
                "fields": "website,formatted_phone_number,name",
                "key": _api_key(),
            },
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get("result", {})
    except Exception as e:
        print(f"  [WARN] Place details failed for {place_id}: {e}")
        return {}


def find_top_competitor(lat: float, lng: float, category: str, own_place_id: str) -> dict | None:
    """Find the highest-reviewed nearby business in the same category."""
    try:
        r = requests.get(
            GOOGLE_PLACES_NEARBY,
            params={
                "location": f"{lat},{lng}",
                "radius": 8000,  # 5 miles
                "keyword": category,
                "key": _api_key(),
            },
            timeout=10,
        )
        r.raise_for_status()
        nearby = r.json().get("results", [])
    except Exception as e:
        print(f"  [WARN] Nearby search failed: {e}")
        return None

    candidates = [
        p for p in nearby
        if p.get("place_id") != own_place_id
        and p.get("user_ratings_total", 0) >= MIN_REVIEWS
    ]
    if not candidates:
        return None

    # Return the one with most reviews (the dominant competitor)
    return max(candidates, key=lambda p: p.get("user_ratings_total", 0))


# ---------------------------------------------------------------------------
# Email scraping
# ---------------------------------------------------------------------------

EMAIL_PATTERN = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE,
)

SKIP_EMAIL_DOMAINS = {
    "sentry.io", "example.com", "wixpress.com", "squarespace.com",
    "wordpress.com", "shopify.com", "adobe.com", "google.com",
    "schema.org", "w3.org", "gravatar.com",
}

PREFERRED_PREFIXES = {"contact", "info", "hello", "office", "admin", "appointments", "booking"}


def scrape_email_from_website(url: str) -> str | None:
    """Try to scrape a contact email from the business website."""
    if not url:
        return None
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; PulseLCI/1.0)"}
        r = requests.get(url, headers=headers, timeout=8, allow_redirects=True)
        text = r.text
    except Exception:
        return None

    emails = EMAIL_PATTERN.findall(text)
    candidates = [
        e for e in emails
        if not any(skip in e.lower() for skip in SKIP_EMAIL_DOMAINS)
        and len(e) < 80
    ]

    if not candidates:
        return None

    # Prefer common contact prefixes
    for email in candidates:
        prefix = email.split("@")[0].lower()
        if prefix in PREFERRED_PREFIXES:
            return email.lower()

    return candidates[0].lower()


# ---------------------------------------------------------------------------
# Chain detection
# ---------------------------------------------------------------------------

def _is_chain(name: str) -> bool:
    name_lower = name.lower()
    return any(chain in name_lower for chain in CHAIN_BLOCKLIST)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _already_exists(place_id: str) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM outreach_prospects WHERE place_id = %s LIMIT 1",
                (place_id,),
            )
            return cur.fetchone() is not None


def _insert_prospect(
    place_id: str,
    business_name: str,
    category: str,
    address: str,
    city: str,
    state: str,
    website: str | None,
    phone: str | None,
    contact_email: str | None,
    reviews_count: int,
    rating: float,
    top_competitor_name: str | None,
    top_competitor_reviews: int | None,
    draft_subject: str,
    draft_body: str,
) -> None:
    status = "draft_ready" if contact_email else "no_email"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO outreach_prospects (
                    place_id, business_name, category, address, city, state,
                    website, phone, contact_email, reviews_count, rating,
                    top_competitor_name, top_competitor_reviews,
                    draft_subject, draft_body, status
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (place_id) DO NOTHING
                """,
                (
                    place_id, business_name, category, address, city, state,
                    website, phone, contact_email, reviews_count, rating,
                    top_competitor_name, top_competitor_reviews,
                    draft_subject, draft_body, status,
                ),
            )
        conn.commit()


# ---------------------------------------------------------------------------
# Main discovery loop
# ---------------------------------------------------------------------------

def discover(city: str, state: str, categories: list[str]) -> None:
    print(f"\n=== Pulse LCI Prospect Discovery ===")
    print(f"City: {city}, {state}")
    print(f"Categories: {', '.join(categories)}\n")

    total_found = 0
    total_inserted = 0

    for category in categories:
        print(f"\n--- Searching: {category} ---")
        places = search_places(category, city, state)
        print(f"  Found {len(places)} raw results")

        for place in places:
            name = place.get("name", "")
            place_id = place.get("place_id", "")
            rating = place.get("rating")
            reviews = place.get("user_ratings_total", 0)
            address = place.get("formatted_address", "")
            geometry = place.get("geometry", {}).get("location", {})
            lat = geometry.get("lat")
            lng = geometry.get("lng")

            # Filter
            if not place_id or not name:
                continue
            if _is_chain(name):
                print(f"  SKIP (chain): {name}")
                continue
            if not rating or not (MIN_RATING <= rating <= MAX_RATING):
                continue
            if not reviews or not (MIN_REVIEWS <= reviews <= MAX_REVIEWS):
                continue
            if _already_exists(place_id):
                print(f"  SKIP (exists): {name}")
                continue

            total_found += 1
            print(f"\n  Processing: {name} ({reviews} reviews, {rating}★)")

            # Get website + phone
            details = get_place_details(place_id)
            website = details.get("website")
            phone = details.get("formatted_phone_number")

            # Find top competitor
            competitor = None
            if lat and lng:
                competitor = find_top_competitor(lat, lng, category, place_id)

            top_competitor_name = None
            top_competitor_reviews = None
            if competitor:
                top_competitor_name = competitor.get("name")
                top_competitor_reviews = competitor.get("user_ratings_total")
                print(f"  Top competitor: {top_competitor_name} ({top_competitor_reviews} reviews)")

            # Scrape email
            contact_email = scrape_email_from_website(website) if website else None
            if contact_email:
                print(f"  Email found: {contact_email}")
            else:
                print(f"  No email found (website: {website or 'none'})")

            # Generate draft
            subject, body = generate_draft(
                business_name=name,
                city=city,
                reviews_count=reviews,
                rating=rating,
                top_competitor_name=top_competitor_name,
                top_competitor_reviews=top_competitor_reviews,
                category=category,
            )

            # Insert
            _insert_prospect(
                place_id=place_id,
                business_name=name,
                category=category,
                address=address,
                city=city,
                state=state,
                website=website,
                phone=phone,
                contact_email=contact_email,
                reviews_count=reviews,
                rating=rating,
                top_competitor_name=top_competitor_name,
                top_competitor_reviews=top_competitor_reviews,
                draft_subject=subject,
                draft_body=body,
            )
            total_inserted += 1
            time.sleep(0.3)  # be polite to Google API

    print(f"\n=== Done ===")
    print(f"Processed: {total_found} prospects")
    print(f"Inserted:  {total_inserted} new records")
    print(f"Run 'python -m outreach.queue' or open the approval UI to review drafts.\n")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Discover cold outreach prospects via Google Places")
    parser.add_argument("--city", required=True, help="City to search (e.g. 'Dallas')")
    parser.add_argument("--state", required=True, help="State abbreviation (e.g. 'TX')")
    parser.add_argument(
        "--categories",
        default=",".join(DEFAULT_CATEGORIES),
        help="Comma-separated list of business categories to search",
    )
    args = parser.parse_args()
    categories = [c.strip() for c in args.categories.split(",") if c.strip()]
    discover(city=args.city, state=args.state, categories=categories)


if __name__ == "__main__":
    main()
