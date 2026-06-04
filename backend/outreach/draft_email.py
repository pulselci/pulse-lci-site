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
    # Normalize spaces and underscores so "auto repair shop" matches "auto_repair"
    normalized = (category or "").lower().replace(" ", "_")
    for key, label in CATEGORIES_FRIENDLY.items():
        if key in normalized or key.replace("_", " ") in category.lower():
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
    stars = f"{rating:.1f}" if rating else ""

    # Build competitive context — one sentence, no em dashes
    if top_competitor_name and top_competitor_reviews is not None:
        gap = reviews - top_competitor_reviews
        if gap > 0:
            comp_line = (
                f"You're ahead of {top_competitor_name}, "
                f"but that lead tends to shrink faster than most owners expect."
            )
        elif gap < 0:
            comp_line = (
                f"You're {abs(gap)} reviews behind {top_competitor_name}. "
                f"Businesses that close gaps like that usually do it with a steady weekly cadence, not one big push."
            )
        else:
            comp_line = (
                f"You and {top_competitor_name} are running about even right now. "
                f"That's when review velocity becomes the tiebreaker."
            )
        competitor_mention = f", and {top_competitor_name} nearby has {top_competitor_reviews}"
    else:
        comp_line = (
            f"Most {cat}s don't have a clear picture of where they actually stand "
            f"relative to their local competition on reviews."
        )
        competitor_mention = ""

    # Build the opening data line
    if reviews and stars:
        data_line = f"You're sitting at {reviews} reviews ({stars} stars){competitor_mention}."
    elif reviews:
        data_line = f"You're sitting at {reviews} reviews{competitor_mention}."
    else:
        data_line = f"I pulled some data on your local market."

    subject = f"Quick question for {business_name}"

    body = f"""Hi,

I was looking at review data for {city} {cat}s and came across {business_name}. {data_line}

{comp_line}

Quick question: is staying on top of your local review position something you're actively working on, or more of a "whenever it happens" situation?

I ask because I built a tool called Pulse LCI that tracks this monthly and sends a report showing exactly where you stand vs. your local competitors and the clearest move to make.

If it sounds useful, you can either reply to this email and I'll pull a free report for you personally, or request one yourself at pulselci.com/#free-report.

Craig White
Founder/CEO, Pulse LCI
craig@pulselci.com
"""

    return subject, body
