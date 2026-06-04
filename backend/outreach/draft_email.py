"""
Email template generator for cold outreach prospects.

Generates short, personalized cold emails using real Google Places data.
No marketing fluff — just reference their actual review count, rating,
and a named competitor to make it feel like a human wrote it.
"""
from __future__ import annotations


CATEGORIES_FRIENDLY = {
    "auto_repair": "auto shop",
    "car_repair": "auto shop",
    "dentist": "dental practice",
    "dental_clinic": "dental practice",
    "medical_spa": "med spa",
    "beauty_salon": "salon",
    "gym": "gym",
    "physiotherapist": "physical therapy practice",
    "chiropractor": "chiropractic office",
    "restaurant": "restaurant",
    "hair_care": "salon",
    "spa": "spa",
    "default": "business",
}


def _friendly_category(category: str | None) -> str:
    if not category:
        return "business"
    for key, label in CATEGORIES_FRIENDLY.items():
        if key in (category or "").lower():
            return label
    return "business"


def generate_draft(
    business_name: str,
    city: str,
    reviews_count: int | None,
    rating: float | None,
    top_competitor_name: str | None,
    top_competitor_reviews: int | None,
    category: str | None = None,
) -> tuple[str, str]:
    """
    Returns (subject, body) for a personalized cold email.
    """
    cat = _friendly_category(category)
    reviews = reviews_count or 0
    stars = f"{rating:.1f}★" if rating else ""

    # Build the competitive context line
    if top_competitor_name and top_competitor_reviews is not None:
        gap = reviews - top_competitor_reviews
        if gap > 0:
            comp_line = (
                f"You're {gap} reviews ahead of {top_competitor_name} — "
                f"the risk for leaders is a challenger quietly accelerating. "
                f"It's worth knowing how fast they're moving."
            )
        elif gap < 0:
            comp_line = (
                f"You're {abs(gap)} reviews behind {top_competitor_name}. "
                f"The businesses that close that gap tend to do it with a consistent weekly cadence, "
                f"not volume bursts."
            )
        else:
            comp_line = (
                f"You and {top_competitor_name} are neck and neck on reviews. "
                f"Moments like this are when velocity decides who pulls ahead."
            )
        competitor_mention = f" and {top_competitor_name} nearby has {top_competitor_reviews}"
    else:
        comp_line = (
            f"Most {cat}s in competitive markets don't realize how much review velocity "
            f"affects which business shows up first — and gets called first."
        )
        competitor_mention = ""

    review_line = ""
    if reviews and stars:
        review_line = f"you're at {reviews} reviews ({stars}){competitor_mention}"
    elif reviews:
        review_line = f"you're at {reviews} reviews{competitor_mention}"
    else:
        review_line = f"I pulled your market data"

    subject = f"Quick question about {business_name}'s review position in {city}"

    body = f"""Hi,

I ran a quick competitive snapshot for {business_name} — {review_line}.

{comp_line}

I built Pulse LCI to track this automatically. You get a monthly report showing exactly where you stand vs. your local competitors and the clearest actions to take — no manual searching.

Here's a free competitive report for {city} {cat}s: https://pulselci.com

Takes about 30 seconds to see your full picture.

— Craig
Pulse LCI | pulselci.com

---
To stop receiving these emails, reply with "unsubscribe" in the subject line.
"""

    return subject, body
