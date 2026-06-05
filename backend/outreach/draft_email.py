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

    # Build gap/lead line and opening
    if top_competitor_name and top_competitor_reviews is not None:
        gap = reviews - top_competitor_reviews
        if gap > 0:
            gap_line = f"That's a lead of {gap} reviews."
            comp_context = f"Leads like that tend to shrink faster than most owners expect."
        elif gap < 0:
            gap_line = f"That's a gap of {abs(gap)} reviews."
            comp_context = f"Businesses that close gaps like that usually do it with a steady weekly cadence, not one big push."
        else:
            gap_line = f"You're running neck and neck."
            comp_context = f"That's when review velocity becomes the tiebreaker."

        if reviews and stars:
            opening = (
                f"I was reviewing {cat}s in {city} and noticed {business_name} has "
                f"{reviews} Google reviews ({stars} stars) while {top_competitor_name} has {top_competitor_reviews}."
            )
        else:
            opening = (
                f"I was reviewing {cat}s in {city} and noticed {business_name} "
                f"while {top_competitor_name} nearby has {top_competitor_reviews} reviews."
            )
    else:
        gap_line = ""
        comp_context = f"Most {cat}s don't have a clear picture of where they stand relative to local competition on reviews."
        if reviews and stars:
            opening = f"I was reviewing {cat}s in {city} and noticed {business_name} has {reviews} Google reviews ({stars} stars)."
        else:
            opening = f"I was reviewing {cat}s in {city} and came across {business_name}."

    subject = f"Quick question for {business_name}"

    body = f"""Hi,

{opening}

{gap_line + chr(10) + chr(10) if gap_line else ""}{comp_context}

Quick question:

Is improving your local review position something you're actively tracking, or more of a "whenever it happens" situation?

I built a tool called Pulse LCI that tracks this automatically and shows exactly where competitors are gaining ground.

If you'd like, reply with the names of your top 3 competitors and I'll send a free report. Or request one at pulselci.com/#free-report.

Craig White
Pulse LCI
"""

    return subject, body
