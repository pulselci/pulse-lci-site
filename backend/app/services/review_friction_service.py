import re
from collections import defaultdict
from typing import Any, Optional


NEGATIVE_THEME_RULES = {
    "speed": {
        "label": "Speed",
        "phrases": [
            "too slow",
            "very slow",
            "slow service",
            "long wait",
            "waited too long",
            "took forever",
            "delayed response",
            "late arrival",
            "behind schedule",
            "slow turnaround",
            "not on time",
        ],
        "tokens": [
            "slow",
            "delay",
            "delayed",
            "late",
            "wait",
            "waiting",
            "forever",
            "behind",
            "turnaround",
        ],
    },
    "communication": {
        "label": "Communication",
        "phrases": [
            "no response",
            "did not respond",
            "never responded",
            "hard to reach",
            "didn't call back",
            "did not call back",
            "poor communication",
            "unclear communication",
            "no update",
            "never heard back",
        ],
        "tokens": [
            "response",
            "responded",
            "communication",
            "communicate",
            "reachable",
            "update",
            "updates",
            "callback",
            "follow-up",
            "followup",
        ],
    },
    "pricing": {
        "label": "Pricing",
        "phrases": [
            "too expensive",
            "way too expensive",
            "hidden fee",
            "hidden fees",
            "surprise charge",
            "surprise charges",
            "not worth it",
            "poor value",
            "over priced",
            "overpriced",
        ],
        "tokens": [
            "expensive",
            "price",
            "pricing",
            "cost",
            "costly",
            "overpriced",
            "fees",
            "fee",
            "charge",
            "charges",
            "value",
        ],
    },
    "professionalism": {
        "label": "Professionalism",
        "phrases": [
            "very rude",
            "extremely rude",
            "unprofessional staff",
            "poor attitude",
            "felt dismissed",
            "not respectful",
            "bad customer service",
            "careless attitude",
        ],
        "tokens": [
            "rude",
            "unprofessional",
            "dismissive",
            "disrespectful",
            "careless",
            "disorganized",
            "attitude",
            "professionalism",
        ],
    },
    "quality": {
        "label": "Quality",
        "phrases": [
            "poor quality",
            "low quality",
            "done incorrectly",
            "not done right",
            "made a mistake",
            "multiple mistakes",
            "sloppy work",
            "bad workmanship",
            "fell apart",
        ],
        "tokens": [
            "quality",
            "mistake",
            "mistakes",
            "sloppy",
            "broken",
            "incorrect",
            "wrong",
            "issue",
            "issues",
            "defect",
            "defective",
        ],
    },
    "scheduling": {
        "label": "Scheduling",
        "phrases": [
            "hard to schedule",
            "difficult to schedule",
            "kept rescheduling",
            "last minute cancellation",
            "cancelled on us",
            "canceled on us",
            "missed appointment",
            "scheduling issue",
            "booking issue",
        ],
        "tokens": [
            "schedule",
            "scheduling",
            "book",
            "booking",
            "cancelled",
            "canceled",
            "rescheduled",
            "appointment",
            "availability",
        ],
    },
}


NEGATIVE_CUE_PHRASES = [
    "not happy",
    "would not recommend",
    "very disappointed",
    "extremely disappointed",
    "poor experience",
    "bad experience",
    "waste of money",
]

NEGATIVE_CUE_TOKENS = [
    "bad",
    "poor",
    "terrible",
    "awful",
    "horrible",
    "disappointed",
    "frustrating",
    "upset",
    "worst",
    "unacceptable",
    "mediocre",
]


def _normalize_text(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9\s'-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _contains_any_phrase(text: str, phrases: list[str]) -> bool:
    return any(p in text for p in phrases)


def _contains_any_token(text: str, tokens: list[str]) -> bool:
    words = set(text.split())
    return any(t in words for t in tokens)


def _is_negative_review(review: dict[str, Any]) -> bool:
    rating = review.get("rating")
    text = _normalize_text(review.get("text", ""))

    if rating is not None:
        try:
            if float(rating) <= 3:
                return True
        except Exception:
            pass

    if _contains_any_phrase(text, NEGATIVE_CUE_PHRASES):
        return True

    if _contains_any_token(text, NEGATIVE_CUE_TOKENS):
        return True

    return False


def _extract_negative_themes(text: str) -> list[str]:
    normalized = _normalize_text(text)
    matched = []

    for theme_key, cfg in NEGATIVE_THEME_RULES.items():
        phrase_hit = _contains_any_phrase(normalized, cfg["phrases"])
        token_hit = _contains_any_token(normalized, cfg["tokens"])
        if phrase_hit or token_hit:
            matched.append(theme_key)

    return matched


def build_review_theme_counts(
    reviews: list[dict[str, Any]],
    owner_competitor_id: Optional[str] = None,
) -> dict[str, Any]:
    competitor_map: dict[str, dict[str, Any]] = {}
    competitor_theme_counts = defaultdict(lambda: defaultdict(int))
    competitor_negative_counts = defaultdict(int)
    competitor_review_counts = defaultdict(int)

    for review in reviews:
        competitor_id = review.get("competitor_id")
        competitor_name = review.get("competitor_name") or "Unknown"
        is_business = bool(review.get("is_business")) or (
            owner_competitor_id is not None and competitor_id == owner_competitor_id
        )

        if not competitor_id:
            continue

        competitor_map[competitor_id] = {
            "competitor_id": competitor_id,
            "competitor_name": competitor_name,
            "is_business": is_business,
        }

        competitor_review_counts[competitor_id] += 1

        if not _is_negative_review(review):
            continue

        competitor_negative_counts[competitor_id] += 1
        matched_themes = _extract_negative_themes(review.get("text", ""))

        for theme_key in set(matched_themes):
            competitor_theme_counts[competitor_id][theme_key] += 1

    competitors = []
    for competitor_id, meta in competitor_map.items():
        theme_counts = {
            theme_key: competitor_theme_counts[competitor_id].get(theme_key, 0)
            for theme_key in NEGATIVE_THEME_RULES.keys()
        }
        competitors.append(
            {
                **meta,
                "review_count_analyzed": competitor_review_counts.get(competitor_id, 0),
                "negative_review_count": competitor_negative_counts.get(competitor_id, 0),
                "theme_counts": theme_counts,
            }
        )

    analyzed_competitors = [c for c in competitors if c["review_count_analyzed"] > 0]
    competitor_count = len(analyzed_competitors) or 1

    owner = next((c for c in competitors if c["is_business"]), None)

    themes = []
    for theme_key, cfg in NEGATIVE_THEME_RULES.items():
        market_total = sum(c["theme_counts"].get(theme_key, 0) for c in competitors)
        market_average = round(market_total / competitor_count, 1)

        leader = max(
            competitors,
            key=lambda c: c["theme_counts"].get(theme_key, 0),
            default=None,
        )

        owner_count = owner["theme_counts"].get(theme_key, 0) if owner else 0
        leader_count = leader["theme_counts"].get(theme_key, 0) if leader else 0

        themes.append(
            {
                "theme_key": theme_key,
                "theme_label": cfg["label"],
                "market_total": market_total,
                "market_average": market_average,
                "leader_competitor_name": leader["competitor_name"] if leader else None,
                "leader_count": leader_count,
                "owner_count": owner_count,
                "owner_vs_market_delta": round(owner_count - market_average, 1),
                "owner_vs_leader_delta": owner_count - leader_count,
            }
        )

    owner_top_themes = []
    if owner:
        owner_top_themes = sorted(
            [
                {
                    "theme_key": t["theme_key"],
                    "theme_label": t["theme_label"],
                    "count": t["owner_count"],
                }
                for t in themes
                if t["owner_count"] > 0
            ],
            key=lambda x: x["count"],
            reverse=True,
        )[:3]

    return {
        "section_title": "Customer Friction Signals",
        "themes": themes,
        "competitors": sorted(
            competitors,
            key=lambda c: (not c["is_business"], c["competitor_name"].lower()),
        ),
        "owner_top_themes": owner_top_themes,
    }


def build_customer_friction_insights(friction_counts: dict) -> list[dict]:
    themes = friction_counts.get("themes") or []
    insights: list[dict] = []
    protected_themes: list[str] = []

    for t in themes:
        theme = t.get("theme_key")
        label = t.get("theme_label")

        owner = t.get("owner_count", 0)
        market_avg = t.get("market_average", 0.0)
        leader = t.get("leader_count", 0)
        leader_name = t.get("leader_competitor_name")

        # Skip completely empty themes
        if owner == 0 and leader == 0:
            continue

        # -------------------------
        # CASE 1: Competitors have issues, you don't
        # Collapse these into one summary insight later
        # -------------------------
        if owner == 0 and leader > 0:
            if label:
                protected_themes.append(label)
            continue

        # -------------------------
        # CASE 2: You are worse than market
        # -------------------------
        if owner > market_avg:
            insights.append({
                "type": "friction_risk",
                "theme": theme,
                "headline": f"You are seeing more {label.lower()} complaints than the market.",
                "summary": (
                    f"You have {owner} {label.lower()} complaints versus a market average of "
                    f"{round(market_avg, 2)}. This is a potential positioning risk."
                ),
                "severity": "negative",
                "sort_order": 1,
            })
            continue

        # -------------------------
        # CASE 3: You have minor presence (but not worse)
        # -------------------------
        if owner > 0:
            insights.append({
                "type": "friction_watch",
                "theme": theme,
                "headline": f"{label} complaints are present but not dominant.",
                "summary": (
                    f"You have {owner} {label.lower()} complaints, but this is not currently above "
                    f"market levels. Monitor for trend changes."
                ),
                "severity": "info",
                "sort_order": 2,
            })
            continue

    # -------------------------
    # Collapsed "you are protected" insight
    # -------------------------
    if protected_themes:
        unique = list(dict.fromkeys(protected_themes))
        joined = ", ".join(t.lower() for t in unique[:3])

        insights.append({
            "type": "friction_protected_summary",
            "theme": "protected_summary",
            "headline": "You are not seeing key complaint patterns affecting competitors.",
            "summary": (
                f"Competitors are seeing complaints across {joined}, while you are not. "
                f"This is currently a broad operational advantage."
            ),
            "severity": "positive",
            "sort_order": 3,
        })

    insights.sort(key=lambda x: x.get("sort_order", 99))
    return insights[:2]


def build_customer_friction_summary(friction_counts: dict, insights: list[dict]) -> str:
    themes = friction_counts.get("themes") or []

    total_negative = sum(t.get("market_total", 0) for t in themes)

    if total_negative == 0:
        return "No meaningful customer friction signals were detected in recent reviews."

    # Find dominant theme
    top = max(themes, key=lambda x: x.get("market_total", 0))
    label = top.get("theme_label")
    total = top.get("market_total", 0)

    if total <= 2:
        return (
            f"Customer complaints are limited in volume. The only emerging signal appears in "
            f"{label.lower()}, but it is not yet a consistent pattern."
        )

    return (
        f"{label} is the most consistent source of customer friction in recent reviews. "
        f"This theme is shaping competitor perception and should be monitored closely."
    )