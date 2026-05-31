from typing import Any, Dict, List, Optional


GENERIC_BAD_PHRASES = [
    "review this item and decide whether it changes messaging",
    "this signal may affect local visibility, trust, or customer choice",
    "identify the highest-impact opportunity from this signal",
    "not every movement matters",
]

BUCKET_GROUPS = {
    "growth": ["leader_gap", "market_share"],
    "positioning": ["perception"],
    "execution": ["friction", "momentum", "other"],
}


def _txt(value: Any) -> str:
    return str(value or "").strip()


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _name(row: Any, fallback: str = "the market leader") -> str:
    if not isinstance(row, dict):
        return fallback
    return row.get("competitor_name") or row.get("name") or fallback


def _reviews(row: Any) -> int:
    if not isinstance(row, dict):
        return 0
    return _safe_int(row.get("reviews_total") or row.get("review_count") or 0)


def _share(row: Any) -> float:
    if not isinstance(row, dict):
        return 0.0
    return _safe_float(row.get("share_pct") or row.get("share") or 0)


def _sov_rows(sections: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(sections, dict):
        return []

    sov = sections.get("share_of_voice") or []

    if isinstance(sov, dict):
        rows = sov.get("rows") or []
    elif isinstance(sov, list):
        rows = sov
    else:
        rows = []

    return [r for r in rows if isinstance(r, dict)]


def _is_owner_leader(sections: Dict[str, Any]) -> bool:
    rows = sorted(_sov_rows(sections), key=_reviews, reverse=True)
    return bool(rows and rows[0].get("is_business"))


def _combined_text(item: Dict[str, Any]) -> str:
    if not isinstance(item, dict):
        return ""

    return " ".join(
        _txt(item.get(k))
        for k in [
            "title",
            "summary",
            "action",
            "detail",
            "why_it_matters",
            "how_to_implement",
            "implication",
            "recommended_action",
        ]
    ).lower()


def _is_generic_or_empty(item: Dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return True

    if item.get("is_fallback"):
        return False

    summary = _txt(item.get("summary") or item.get("title"))
    action = _txt(item.get("action") or item.get("recommended_action"))

    if not summary and not action:
        return True

    combined = _combined_text(item)
    return any(phrase in combined for phrase in GENERIC_BAD_PHRASES)


def _insight_bucket(item: Dict[str, Any]) -> str:
    text = _combined_text(item)

    if any(x in text for x in ["trail", "behind", "gap", "leader", "ranked #", "rank #"]):
        return "leader_gap"

    if any(x in text for x in ["controls", "review share", "share of voice", "dominant competitor"]):
        return "market_share"

    if any(x in text for x in ["perception", "customer language", "friendly staff", "speed", "convenience", "positioning"]):
        return "perception"

    if any(x in text for x in ["friction", "complaint", "scheduling", "wait", "slow"]):
        return "friction"

    if any(x in text for x in ["momentum", "gained", "lost", "widened", "pulling away"]):
        return "momentum"

    return "other"


def _bucket_group(bucket: str) -> str:
    for group, buckets in BUCKET_GROUPS.items():
        if bucket in buckets:
            return group
    return "execution"


def _clean_generic_phrase(item: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(item, dict):
        return item

    item = dict(item)

    for key in ["summary", "action", "detail", "how_to_implement"]:
        val = str(item.get(key) or "")

        if "position directly against" in val.lower():
            item[key] = (
                "Win customers from the market leader by highlighting clear differentiators, "
                "stronger trust signals, and clear reasons customers should choose you."
            )

    return item


def clean_insight_list(
    items: Any,
    max_per_bucket: int = 1,
    max_items: int = 8,
) -> List[Dict[str, Any]]:
    if not isinstance(items, list):
        return []

    cleaned = []
    bucket_counts = {}

    for item in items:
        if not isinstance(item, dict) or _is_generic_or_empty(item):
            continue

        item = _clean_generic_phrase(item)

        bucket = _insight_bucket(item)
        bucket_counts[bucket] = bucket_counts.get(bucket, 0)

        if bucket_counts[bucket] >= max_per_bucket and bucket != "other":
            continue

        bucket_counts[bucket] += 1
        cleaned.append(item)

        if len(cleaned) >= max_items:
            break

    return cleaned


def select_diverse_focus_items(items: Any) -> List[Dict[str, Any]]:
    cleaned = clean_insight_list(items, max_per_bucket=2, max_items=10)
    selected = []
    used_groups = set()

    for item in cleaned:
        group = _bucket_group(_insight_bucket(item))

        if group not in used_groups:
            selected.append(item)
            used_groups.add(group)

        if len(selected) >= 3:
            break

    return selected


def build_position_context_from_sov(sections: Dict[str, Any]) -> List[str]:
    rows = sorted(_sov_rows(sections), key=_reviews, reverse=True)

    if not rows:
        return []

    you = next((r for r in rows if r.get("is_business")), None)
    leader = rows[0]

    if not isinstance(you, dict) or not isinstance(leader, dict):
        return []

    you_reviews = _reviews(you)
    leader_reviews = _reviews(leader)
    you_share = _share(you)
    leader_share = _share(leader)
    leader_name = _name(leader, "the market leader")

    context = []

    if leader.get("is_business"):
        context.append(
            f"You lead the market with {you_reviews:,} reviews and roughly {you_share:.0f}% review share."
        )

        if len(rows) > 1:
            challenger = rows[1]
            context.append(
                f"{_name(challenger, 'The closest challenger')} is the closest challenger at {_reviews(challenger):,} reviews, so protect the lead by keeping review generation consistent."
            )

    else:
        gap = max(leader_reviews - you_reviews, 0)

        context.append(
            f"You are chasing {leader_name}, who leads the market with {leader_reviews:,} reviews and roughly {leader_share:.0f}% review share."
        )
        context.append(
            f"You currently have {you_reviews:,} reviews, leaving a {gap:,}-review gap to close."
        )

        below_you = [
            r for r in rows
            if not r.get("is_business") and _reviews(r) < you_reviews
        ]

        if below_you:
            below = below_you[0]
            context.append(
                f"{_name(below, 'The closest challenger')} is {you_reviews - _reviews(below):,} reviews behind you, so protect your position while closing the gap above."
            )

    return context[:3]


def ensure_minimum_focus_items(
    report_experience: Dict[str, Any],
    sections: Dict[str, Any],
) -> None:
    focus_items = report_experience.get("this_month_focus") or []

    if not isinstance(focus_items, list):
        focus_items = []

    is_leader = _is_owner_leader(sections)

    # Remove duplicated focus items and leader-inappropriate gap language
    cleaned_focus = []
    seen = set()

    for item in focus_items:
        if not isinstance(item, dict):
            continue

        text = _combined_text(item)

        if is_leader and any(
            phrase in text
            for phrase in ["close the gap", "within striking distance", "trail the market leader", "gap with the market leader"]
        ):
            continue

        item = _clean_generic_phrase(item)
        key = (str(item.get("action") or item.get("summary") or item.get("title") or "")).lower()

        if key and key not in seen:
            cleaned_focus.append(item)
            seen.add(key)

    focus_items = cleaned_focus[:3]

    rows = sorted(_sov_rows(sections), key=_reviews, reverse=True)
    leader = rows[0] if rows else None
    leader_name = _name(leader, "the market leader")

    if is_leader:
        fallback_pool = [
            {
                "title": "Protect and extend your lead with consistent review growth.",
                "summary": "Protect and extend your lead with consistent review growth.",
                "action": "Protect and extend your lead with consistent review growth.",
                "priority": "Immediate",
                "detail": "Maintain a steady review request process and track monthly gains against your closest competitor.",
                "bucket_group": "growth",
            },
            {
                "title": "Define one clear advantage competitors cannot easily copy.",
                "summary": "Define one clear advantage competitors cannot easily copy.",
                "action": "Highlight one clear advantage customers should associate with your business.",
                "priority": "Immediate",
                "detail": "Reinforce one strength such as trust, comfort, convenience, or communication across your website and Google profile.",
                "bucket_group": "positioning",
            },
            {
                "title": "Make your credibility obvious at the decision point.",
                "summary": "Make your credibility obvious at the decision point.",
                "action": "Improve how your reviews and credibility are presented.",
                "priority": "Next",
                "detail": "Feature top reviews prominently, respond to reviews, and highlight credentials or guarantees.",
                "bucket_group": "execution",
            },
        ]
    else:
        fallback_pool = [
            {
                "title": "Close the review gap with a structured monthly target.",
                "summary": "Close the review gap with a structured monthly target.",
                "action": "Set a monthly review goal tied to the gap and track progress weekly.",
                "priority": "Next",
                "detail": "Ask for reviews after every completed job, use SMS/email follow-ups, and track weekly progress toward your target.",
                "bucket_group": "growth",
            },
            {
                "title": f"Win customers from {leader_name} by clearly positioning around your strongest advantage.",
                "summary": f"Win customers from {leader_name} by clearly positioning around your strongest advantage.",
                "action": "Highlight one clear advantage customers should choose you for.",
                "priority": "Immediate",
                "detail": "Update your homepage and Google Business Profile to reinforce one key advantage such as speed, trust, or convenience.",
                "bucket_group": "positioning",
            },
            {
                "title": "Increase conversion by strengthening trust signals.",
                "summary": "Increase conversion by strengthening trust signals.",
                "action": "Improve how your reviews and credibility are presented.",
                "priority": "Next",
                "detail": "Feature top reviews prominently, respond to all new reviews, and highlight guarantees, certifications, or proof points that reduce customer hesitation.",
                "bucket_group": "execution",
            },
        ]

    existing_groups = {
        _bucket_group(_insight_bucket(item))
        for item in focus_items
        if isinstance(item, dict)
    }

    for item in fallback_pool:
        if len(focus_items) >= 3:
            break

        group = item.get("bucket_group") or "execution"

        if group not in existing_groups:
            focus_items.append(item)
            existing_groups.add(group)

    report_experience["this_month_focus"] = focus_items[:3]


def _leader_fallback_recommendations(sections: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    # Pull real numbers from SOV data if available
    sov = (sections or {}).get("share_of_voice") or {}
    rows = sov.get("rows") or []

    owner_reviews: int = 0
    owner_share: float = 0.0
    challenger_name: str = "your closest competitor"
    challenger_reviews: int = 0

    for row in rows:
        if isinstance(row, dict) and row.get("is_business"):
            owner_reviews = int(row.get("reviews_total") or 0)
            owner_share = round(float(row.get("share_pct") or 0), 1)
            break

    for row in rows:
        if isinstance(row, dict) and not row.get("is_business") and int(row.get("rank") or 99) == 2:
            challenger_name = row.get("competitor_name") or "your closest competitor"
            challenger_reviews = int(row.get("reviews_total") or 0)
            break

    gap = max(0, owner_reviews - challenger_reviews) if owner_reviews and challenger_reviews else 0
    gap_threshold = max(100, gap // 3)

    # ── Card 1: defend the lead ────────────────────────────────────────────
    if owner_reviews and challenger_reviews:
        why_defend = (
            f"{challenger_name} holds {challenger_reviews:,} reviews — {gap:,} behind you. "
            f"That gap shrinks if you stop growing and they don't."
        )
        how_defend = (
            f"Aim for at least 10 new reviews per month. "
            f"If {challenger_name} closes to within {gap_threshold:,} reviews of you, increase your cadence immediately."
        )
    else:
        why_defend = "Without consistent growth, competitors can close the gap over time."
        how_defend = "Maintain a steady review request process and track monthly gains against your closest competitor."

    # ── Card 2: positioning ────────────────────────────────────────────────
    if owner_reviews and owner_share:
        why_position = (
            f"You hold {owner_share}% of all market reviews, but no single competitor owns a clear perception advantage. "
            "That's a window — claim a position before someone else does."
        )
    else:
        why_position = "As the leader, owning a clear position makes your advantage harder to challenge."

    # ── Card 3: credibility ────────────────────────────────────────────────
    if owner_reviews:
        why_credibility = (
            f"With {owner_reviews:,} reviews and the #1 ranking, your lead should be visible to every patient comparing options. "
            "Displaying it prominently turns your position into a conversion advantage."
        )
        how_credibility = (
            f"Pin your strongest 2–3 reviews on your Google profile, respond to every new review within 48 hours, "
            f"and display your review count and star rating on your website homepage."
        )
    else:
        why_credibility = "Clear proof reinforces your leadership and reduces customer hesitation."
        how_credibility = "Feature top reviews, respond to all reviews, and highlight credentials or guarantees prominently."

    # ── Card 4: monitor challenger ─────────────────────────────────────────
    challenger_label = challenger_name if challenger_name != "your closest competitor" else "your closest competitor"

    return [
        {
            "summary": "You lead the market, but competitors are close enough to challenge your position.",
            "action": "Protect and extend your lead by maintaining consistent review growth.",
            "priority": "Immediate",
            "why_it_matters": why_defend,
            "how_to_implement": how_defend,
            "is_fallback": True,
        },
        {
            "summary": "No competitor clearly owns a dominant customer perception.",
            "action": "Define and reinforce a clear positioning advantage before competitors do.",
            "priority": "Immediate",
            "why_it_matters": why_position,
            "how_to_implement": "Highlight one strength — trust, comfort, convenience, or communication — across your messaging and review responses.",
            "is_fallback": True,
        },
        {
            "summary": "Strong leaders win by making credibility obvious at the decision point.",
            "action": "Improve how your reviews and trust signals are presented.",
            "priority": "Next",
            "why_it_matters": why_credibility,
            "how_to_implement": how_credibility,
            "is_fallback": True,
        },
        {
            "summary": f"Your closest challenger, {challenger_label}, is large enough to monitor closely.",
            "action": f"Track your lead over {challenger_label} monthly and act quickly if the gap starts shrinking.",
            "priority": "Next",
            "why_it_matters": "Market leaders lose position when they stop watching challenger momentum.",
            "how_to_implement": f"Compare monthly review gains against {challenger_label} and adjust your review request rate if they start outpacing you.",
            "is_fallback": True,
        },
    ]


def _challenger_fallback_recommendations() -> List[Dict[str, Any]]:
    return [
        {
            "summary": "You are within striking distance, but closing the gap requires a sustained review growth advantage.",
            "action": "Set a monthly review target tied to the gap with the market leader.",
            "priority": "Immediate",
            "why_it_matters": "Without a clear growth target, the gap remains static or widens over time.",
            "how_to_implement": "Set a monthly goal tied to the gap, request reviews after every job, and track weekly progress.",
            "is_fallback": True,
        },
        {
            "summary": "No competitor clearly owns a dominant customer perception.",
            "action": "Define and consistently communicate one clear advantage customers should associate with your business.",
            "priority": "Immediate",
            "why_it_matters": "When no competitor owns a position, the first to claim one gains a decision-making advantage.",
            "how_to_implement": "Choose one value such as faster turnaround, clearer communication, or more transparent pricing and reinforce it across your website, Google profile, and review responses.",
            "is_fallback": True,
        },
        {
            "summary": "Stronger competitors often win by appearing more credible at the decision point.",
            "action": "Improve how your reviews and trust signals are presented.",
            "priority": "Next",
            "why_it_matters": "Customers compare options quickly. Clear proof reduces hesitation and increases conversions.",
            "how_to_implement": "Feature top reviews, respond to all new reviews, and highlight guarantees or certifications prominently.",
            "is_fallback": True,
        },
        {
            "summary": "Competitors below you are close enough to apply pressure on your position.",
            "action": "Maintain consistent review generation to protect your current ranking.",
            "priority": "Next",
            "why_it_matters": "Without steady growth, competitors can close the gap from below.",
            "how_to_implement": "Track monthly review gains and ensure consistent request processes across all customer interactions.",
            "is_fallback": True,
        },
    ]


def ensure_minimum_recommendations(
    report_experience: Dict[str, Any],
    sections: Dict[str, Any],
) -> None:
    flat = report_experience.get("flat_insights") or []

    if not isinstance(flat, list):
        flat = []

    is_leader = _is_owner_leader(sections)

    cleaned = []
    seen_growth = False

    for item in flat:
        if not isinstance(item, dict):
            continue

        item = _clean_generic_phrase(item)
        text = _combined_text(item)

        # Leaders should never get challenger/gap recommendations.
        if is_leader and any(
            phrase in text
            for phrase in ["within striking distance", "close the gap", "trail the market leader", "gap with the market leader"]
        ):
            continue

        is_growth = (
            "review target" in text
            or ("gap" in text and "review" in text)
            or "review growth" in text
            or "closing the gap" in text
        )

        # Keep only one growth-style card.
        if is_growth:
            if seen_growth:
                continue
            seen_growth = True

        cleaned.append(item)

    flat = cleaned

    fallback = (
        _leader_fallback_recommendations(sections)
        if is_leader
        else _challenger_fallback_recommendations()
    )

    existing_text = {
        (str(i.get("summary") or "") + " " + str(i.get("action") or "")).lower()
        for i in flat
        if isinstance(i, dict)
    }

    for item in fallback:
        if len(flat) >= 4:
            break

        item_text = (str(item.get("summary") or "") + " " + str(item.get("action") or "")).lower()

        if item_text not in existing_text:
            flat.append(item)
            existing_text.add(item_text)

    report_experience["flat_insights"] = flat[:4]


def apply_report_integrity_rules(
    report_experience: Dict[str, Any],
    sections: Dict[str, Any],
) -> Dict[str, Any]:
    if not isinstance(report_experience, dict):
        report_experience = {}

    if not isinstance(sections, dict):
        sections = {}

    report_experience["flat_insights"] = clean_insight_list(
        report_experience.get("flat_insights"),
        max_per_bucket=2,
        max_items=8,
    )

    report_experience["position_context"] = build_position_context_from_sov(sections)

    summary_side = report_experience.get("summary_side") or {}
    if not isinstance(summary_side, dict):
        summary_side = {}

    summary_side["position_context"] = report_experience["position_context"]
    report_experience["summary_side"] = summary_side

    report_experience["this_month_focus"] = select_diverse_focus_items(
        report_experience.get("this_month_focus")
    )

    ensure_minimum_focus_items(report_experience, sections)
    ensure_minimum_recommendations(report_experience, sections)

    flat = report_experience.get("flat_insights") or []

    report_experience["grouped_sections"] = [
        {
            "section": "Strategic Recommendations",
            "insights": flat[:4],
        }
    ]

    return report_experience