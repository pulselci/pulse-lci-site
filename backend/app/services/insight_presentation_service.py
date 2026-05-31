from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple


Insight = Dict[str, Any]


SECTION_POSITIONING = "Positioning Opportunities"
SECTION_THREATS = "Competitive Threats"
SECTION_MESSAGING = "Messaging Gaps"
SECTION_PERCEPTION = "Customer Perception Summary"
SECTION_MARKET = "Market Dynamics"
SECTION_OTHER = "Other Signals"


SECTION_ORDER = [
    SECTION_POSITIONING,
    SECTION_THREATS,
    SECTION_MESSAGING,
    SECTION_PERCEPTION,
    SECTION_MARKET,
    SECTION_OTHER,
]


TYPE_TO_SECTION = {
    "praise_themes": SECTION_PERCEPTION,
    "complaint_themes": SECTION_PERCEPTION,
    "perception_rollup": SECTION_PERCEPTION,
    "complaint_rollup": SECTION_PERCEPTION,
    "hidden_opportunity": SECTION_POSITIONING,
    "messaging_mismatch": SECTION_MESSAGING,
    "messaging_rollup": SECTION_MESSAGING,
    "competitive_tier_pressure": SECTION_THREATS,
    "challenger_gap": SECTION_THREATS,
    "leader_pulling_away": SECTION_THREATS,
    "competitor_surge": SECTION_THREATS,
    "threats": SECTION_THREATS,
    "position_change": SECTION_MARKET,
    "market_movers": SECTION_MARKET,
    "market_concentration": SECTION_MARKET,
    "market_quiet": SECTION_MARKET,
    "share_of_voice": SECTION_MARKET,
    "velocity_trends": SECTION_MARKET,
    "momentum": SECTION_MARKET,
    "top_moves": SECTION_MARKET,
    "weekly_actions": SECTION_POSITIONING,
    "baseline_rank": SECTION_MARKET,
    "market_position": SECTION_MARKET,
    "market_dominance": SECTION_PERCEPTION,
}


TYPE_PRIORITY_HINT = {
    "hidden_opportunity": "Immediate",
    "messaging_mismatch": "Immediate",
    "messaging_rollup": "Immediate",
    "competitive_tier_pressure": "Immediate",
    "leader_pulling_away": "Immediate",
    "competitor_surge": "Immediate",
    "challenger_gap": "Next",
    "complaint_themes": "Next",
    "complaint_rollup": "Next",
    "praise_themes": "Next",
    "perception_rollup": "Next",
    "position_change": "Next",
    "market_movers": "Next",
    "market_concentration": "Monitor",
    "market_quiet": "Monitor",
    "share_of_voice": "Monitor",
    "velocity_trends": "Monitor",
    "momentum": "Monitor",
    "top_moves": "Monitor",
    "weekly_actions": "Immediate",
    "baseline_rank": "Next",
    "market_position": "Next",
    "market_dominance": "Next",
}


SEVERITY_TO_PRIORITY = {
    "critical": "Immediate",
    "warning": "Immediate",
    "info": "Next",
}


RAW_PRIORITY_TO_DISPLAY = {
    "high": "Immediate",
    "immediate": "Immediate",
    "urgent": "Immediate",
    "medium": "Next",
    "next": "Next",
    "low": "Monitor",
    "monitor": "Monitor",
}


CHANGE_AWARE_TYPES = {
    "competitive_tier_pressure",
    "challenger_gap",
    "leader_pulling_away",
    "competitor_surge",
    "position_change",
    "market_movers",
    "market_concentration",
    "market_quiet",
    "share_of_voice",
    "velocity_trends",
    "momentum",
    "top_moves",
    "baseline_rank",
    "market_position",
    "market_dominance",
}

def _build_executive_summary(
    ordered: List[Insight],
) -> str:
    """
    Deterministic 3-part executive summary:
    1. You win on X.
    2. Competitor wins on Y.
    3. Do Z.
    """

    if not ordered:
        return "No major competitive signals surfaced this cycle."

    owner_strengths = ""
    competitor_strengths = ""
    competitor_name = ""
    primary_action = ""

    for item in ordered:
        summary = str(item.get("summary") or "").strip()
        action = str(item.get("action") or "").strip().rstrip(".")
        details = item.get("details") or {}

        if not competitor_name:
            competitor_name = str(
                details.get("competitor_name")
                or details.get("leader_name")
                or details.get("challenger_name")
                or ""
            ).strip()

        lower_summary = summary.lower()

        if not owner_strengths and "you are outperforming on " in lower_summary:
            after = summary.split("you are outperforming on ", 1)[1]
            owner_strengths = after.split(", while", 1)[0].strip().rstrip(".")

        if not competitor_strengths and "winning on " in lower_summary:
            after = summary.split("winning on ", 1)[1]
            competitor_strengths = after.split(".", 1)[0].strip().rstrip(".")

        if not primary_action and action:
            primary_action = action

    if not owner_strengths:
        owner_strengths = "key service advantages"

    if not competitor_strengths:
        competitor_strengths = "strong market positioning"

    if not competitor_name:
        competitor_name = "your top competitor"

    if not primary_action:
        primary_action = "Focus on strengthening your positioning"

    return (
        f"You win on {owner_strengths}.\n"
        f"{competitor_name} wins on {competitor_strengths}.\n"
        f"{primary_action}."
    )

def build_this_month_focus(
    action_plan: dict | None,
    sections: dict | None = None,
) -> list[dict]:
    """
    Build a client-centric 'This Month's Focus' section.

    Rules:
    - Max 3 items
    - Only include items directly relevant to the client
    - Avoid general market trivia
    - Prefer positioning, rank pressure, and client gap signals
    - Never show challenger urgency language to a market leader
    """

    if not action_plan or not isinstance(action_plan, dict):
        return []

    immediate_items = action_plan.get("immediate") or []
    next_items = action_plan.get("next") or []
    all_items = immediate_items + next_items

    focus_items: list[dict] = []
    seen_types: set[str] = set()

    # ── Leader detection ─────────────────────────────────────────────────
    # Prefer hard data from SOV rows (rank == 1) over text scanning.
    # Fall back to text scanning only if SOV data is unavailable.
    def _detect_owner_is_leader() -> bool:
        sov = (sections or {}).get("share_of_voice") or {}
        rows = sov.get("rows") or []
        for row in rows:
            if row.get("is_business"):
                return int(row.get("rank") or 99) == 1
        # Most reliable signal: baseline_rank insight stores owner_rank directly
        # from the database — immune to text enrichment bugs.
        all_plan_items = [
            *(action_plan.get("immediate") or []),
            *(action_plan.get("next") or []),
            *(action_plan.get("monitor") or []),
        ]
        for item in all_plan_items:
            if str(item.get("type") or "").lower() == "baseline_rank":
                details = item.get("details") or {}
                owner_rank = details.get("owner_rank")
                if owner_rank is not None:
                    return int(owner_rank) == 1
        # Last resort: scan insight text across all plan items
        leader_phrases = (
            "you currently lead",
            "you lead the market",
            "lead the market",
            "leading the market by",
            "ranked #1",
            "rank #1",
            "leading position",
            "you are ranked #1",
            "#1 position",
            "your #1",
            "defend your",
            "protect the lead",
            "protect your lead",
            "extend your lead",
            "currently hold the leading",
        )
        return any(
            any(phrase in str(item).lower() for phrase in leader_phrases)
            for item in all_plan_items
        )

    owner_is_leader = _detect_owner_is_leader()
    print(f"[TMF] owner_is_leader={owner_is_leader} | baseline_in_plan={any(str(i.get('type','')).lower()=='baseline_rank' for i in [*(action_plan.get('immediate') or []),*(action_plan.get('next') or []),*(action_plan.get('monitor') or [])])}")

    # ── Closest competitor name ───────────────────────────────────────────
    closest_competitor = None
    for item in all_items:
        details = item.get("details") or {}
        if isinstance(details, dict):
            closest_competitor = (
                details.get("challenger_name")
                or details.get("leader_name")
                or details.get("competitor_name")
                or closest_competitor
            )
    closest_competitor = closest_competitor or "your closest competitor"

    # ── Challenger language detector ──────────────────────────────────────
    # Any action containing these phrases is inappropriate for a leader.
    _CHALLENGER_PHRASES = (
        "before the gap becomes harder to close",
        "close the gap",
        "closing the gap",
        "close the review gap",
        "closing distance to the leader",
        "close distance to the leader",
        "catch up",
        "catch the leader",
        "gain ground on",
        "to overtake",
        "within striking distance",
        "trail the leader",
        "you are behind",
        "you trail",
        "respond quickly",          # urgency framing implying you're losing
        "before competitors catch",
        "before the gap widens",
    )

    def _is_challenger_language(text: str) -> bool:
        t = (text or "").lower()
        return any(phrase in t for phrase in _CHALLENGER_PHRASES)

    # ── Leader-appropriate replacements ───────────────────────────────────
    _LEADER_ACTIONS = [
        f"Maintain consistent review growth to protect your lead over {closest_competitor}.",
        "Define and reinforce the one advantage that makes you the obvious choice in your market.",
        "Make your credibility clear at the decision point — feature top reviews and trust signals prominently.",
    ]

    def _leader_safe_action(raw_action: str, slot_index: int) -> str:
        """Return a leader-appropriate action for the given slot."""
        return _LEADER_ACTIONS[min(slot_index, len(_LEADER_ACTIONS) - 1)]

    # ── Text cleanup ──────────────────────────────────────────────────────
    def _clean(text: str) -> str:
        text = (text or "").strip()
        text = text.replace("Positioning opening:", "").strip()
        if "Reposition by" in text:
            text = text.split("Reposition by", 1)[0].strip().rstrip(".")
        text = text.replace("you are outperforming on", "Your strongest edge is")
        text = text.replace(", while ", ", but ")
        text = text.replace(" is winning on ", " wins on ")
        text = text.replace(", ,", ",").replace(",,", ",")
        if text and not text.endswith("."):
            text += "."
        return text

    # ── _add closure ─────────────────────────────────────────────────────
    def _add(item: dict, signal_type: str, priority: str) -> None:
        summary = str(item.get("summary") or "").strip()
        summary_lower = summary.lower()
        raw_action = str(item.get("action") or item.get("recommended_action") or "").strip()

        if not raw_action or raw_action.lower() == summary_lower:
            # Build action from summary context
            if "controls" in summary_lower or "top 2 competitors" in summary_lower:
                action = "Make speed and convenience the clearest reason customers choose you over competitors."
            elif "leading the market by" in summary_lower:
                action = f"Maintain consistent review growth to protect your lead over {closest_competitor}."
            elif ("behind" in summary_lower or "gap" in summary_lower) and not owner_is_leader:
                action = f"Close the gap with consistent review growth and sharper positioning against {closest_competitor}."
            elif "ranked" in summary_lower or "tier" in summary_lower:
                action = "Adjust your review and positioning strategy based on your current rank to move up one position."
            else:
                action = "Strengthen the customer-facing advantage most likely to influence buyer choice this month."
        else:
            # raw_action exists — filter challenger language for leaders
            if owner_is_leader and _is_challenger_language(raw_action):
                action = _leader_safe_action(raw_action, len(focus_items))
            else:
                action = raw_action

        action = _clean(str(action))

        if not action:
            return
        if signal_type in seen_types and len(focus_items) >= 3:
            return

        action_key = action.lower()
        existing_actions = {str(i.get("action") or "").lower() for i in focus_items}
        if action_key in existing_actions:
            return

        focus_items.append(
            {
                "title": action,
                "summary": action,
                "action": action,
                "priority": priority,
                "detail": _build_execution_detail(action),
            }
        )
        seen_types.add(signal_type)

    # ── Pass 1: Positioning focus ─────────────────────────────────────────
    for item in all_items:
        _add(item, "positioning", item.get("priority") or "Immediate")
        break

    # ── Pass 2: Pressure focus ────────────────────────────────────────────
    added = False
    for item in all_items:
        if str(item.get("type") or "").strip().lower() == "competitive_tier_pressure":
            _add(item, "pressure", item.get("priority") or "Immediate")
            added = True
            break
    if not added:
        for item in all_items:
            summary = str(item.get("summary") or "").lower()
            if "ahead" in summary or "below" in summary or "pressure" in summary:
                _add(item, "pressure", item.get("priority") or "Next")
                break

    # ── Pass 3: Gap focus (only if it directly names you) ─────────────────
    for item in all_items:
        insight_type = str(item.get("type") or "").strip().lower()
        summary_lower = (item.get("summary") or "").lower()
        if insight_type in {"challenger_gap", "market_dominance"}:
            if "you" in summary_lower or "your" in summary_lower:
                _add(item, "gap", item.get("priority") or "Immediate")
                break

    # ── Fallback fill ─────────────────────────────────────────────────────
    if len(focus_items) < 3:
        existing_actions = {str(i.get("action") or "").strip().lower() for i in focus_items}
        existing_decisions = {_derive_decision({"action": i.get("action")}) for i in focus_items}

        for item in all_items:
            raw_action = str(item.get("action") or item.get("recommended_action") or "").strip()
            if not raw_action:
                continue
            # Filter challenger language here too
            if owner_is_leader and _is_challenger_language(raw_action):
                raw_action = _leader_safe_action(raw_action, len(focus_items))
            action = _clean(raw_action)
            action_key = action.lower()
            decision = _derive_decision({"action": action})
            if not action or action_key in existing_actions or decision in existing_decisions:
                continue
            focus_items.append(
                {
                    "title": action,
                    "summary": action,
                    "action": action,
                    "priority": item.get("priority") or "Next",
                    "detail": _build_execution_detail(action),
                }
            )
            existing_actions.add(action_key)
            existing_decisions.add(decision)
            if len(focus_items) >= 3:
                break

    # ── Leader-specific fill ──────────────────────────────────────────────
    if owner_is_leader and len(focus_items) < 3:
        existing_actions = {str(i.get("action") or "").lower() for i in focus_items}
        for leader_action in _LEADER_ACTIONS:
            cleaned = _clean(leader_action)
            if cleaned.lower() not in existing_actions:
                focus_items.append(
                    {
                        "title": cleaned,
                        "summary": cleaned,
                        "action": cleaned,
                        "priority": "Immediate",
                        "detail": _build_execution_detail(cleaned),
                    }
                )
                existing_actions.add(cleaned.lower())
                if len(focus_items) >= 3:
                    break

    # ── Generic fill (non-leader) ─────────────────────────────────────────
    if not owner_is_leader and len(focus_items) < 3:
        generic = "Own one customer value clearly and reinforce it across all messaging."
        existing_actions = {str(i.get("action") or "").lower() for i in focus_items}
        if generic.lower() not in existing_actions:
            focus_items.append(
                {
                    "title": generic,
                    "summary": generic,
                    "action": generic,
                    "priority": "Next",
                    "detail": "Choose one value (speed, trust, or convenience), highlight it on your homepage, and reinforce it through reviews and customer communication.",
                }
            )

    # ── Final dedupe ──────────────────────────────────────────────────────
    deduped = []
    seen = set()
    for item in focus_items:
        action = str(item.get("action") or "").lower().strip()
        if action in seen:
            continue
        seen.add(action)
        deduped.append(item)

    # ── Post-processing: last-line safety net for leader ──────────────────
    # Catches anything that slipped through all the above filters.
    if owner_is_leader:
        cleaned_final = []
        for i, item in enumerate(deduped):
            action = str(item.get("action") or "")
            if _is_challenger_language(action):
                replacement = _clean(_leader_safe_action(action, i))
                if not any(str(x.get("action") or "").lower() == replacement.lower() for x in cleaned_final):
                    item = dict(item)
                    item["title"] = replacement
                    item["summary"] = replacement
                    item["action"] = replacement
                    item["detail"] = _build_execution_detail(replacement)
            cleaned_final.append(item)
        deduped = cleaned_final

    return deduped[:3]

def _derive_decision(item: dict) -> str:
    text = " ".join([
        str(item.get("summary") or ""),
        str(item.get("title") or ""),
        str(item.get("action") or ""),
        str(item.get("section") or ""),
    ]).lower()

    if "messaging" in text or "customer language" in text or "website messaging" in text:
        return "improve_conversion"

    if "behind" in text or "gap" in text or "trail" in text:
        return "close_gap"

    if "leader" in text or "controls" in text or "top 2" in text:
        return "challenge_leader"

    if "upper tier" in text or "ranked #3" in text or "ranked #2" in text:
        return "push_for_top"

    if "pressure" in text or "ahead by" in text or "below" in text:
        return "defend_position"

    if "perception" in text or "trust" in text or "speed" in text or "quality" in text:
        return "win_positioning"

    return "general"


def _build_strategic_action(item: dict) -> str:
    decision = _derive_decision(item)

    if decision == "improve_conversion":
        details = item.get("details") or {}

        # try to extract themes from perception data
        themes = []

        for key in [
            "owner_winning_themes",
            "owner_themes",
            "winning_themes",
            "primary_theme",
            "theme"
        ]:
            value = details.get(key)
            if isinstance(value, list) and value:
                themes = [str(v).strip().lower() for v in value if str(v).strip()]
                break
            if isinstance(value, str) and value.strip():
                themes = [v.strip().lower() for v in value.split(",") if v.strip()]
                break

        if themes:
            top_themes = ", ".join([t.title() for t in themes[:2]])
            return f"Update messaging to reflect what customers consistently value: {top_themes}."

        return "Update messaging to reflect how customers describe their experience to improve conversion."

    if decision == "close_gap":
        summary = str(item.get("summary") or "")

        # try to extract competitor name + gap
        competitor = ""
        gap = ""

        import re

        match = re.search(r"trail ([\w\s\.'-]+) by (\d+)", summary.lower())
        if match:
            competitor = match.group(1).strip().title()
            gap = match.group(2)

        if competitor and gap:
            return f"Close the {gap}-review gap with {competitor} by increasing monthly review volume."
        
        return "Increase review velocity to close the gap with higher-ranked competitors."

    if decision == "challenge_leader":
        return "Win customers from the market leader by highlighting clear differentiators and clear reasons customers should choose you."

    if decision == "push_for_top":
        return "Use your upper-tier position to challenge more directly for the top spot."

    if decision == "defend_position":
        return "Protect your current position by maintaining consistent review growth and visibility."

    if decision == "win_positioning":
        return "Own one customer value clearly, such as speed, trust, convenience, or quality."

    return str(item.get("action") or item.get("summary") or "").strip()


def _build_execution_steps(item: dict) -> str:
    decision = _derive_decision(item)

    if decision == "improve_conversion":
        return (
            "Update the homepage headline first, then service page headers, then testimonials so the same "
            "customer-valued themes appear consistently across buyer touchpoints."
        )

    if decision == "close_gap":
        return (
            "Set a monthly review target, request reviews after every completed job, and track progress weekly "
            "against the competitors directly above you."
        )

    if decision == "challenge_leader":
        return (
            "Create one comparison message against the market leader, feature your strongest proof points, "
            "and make the difference obvious before buyers contact either business."
        )

    if decision == "push_for_top":
        return (
            "Choose one clear advantage to elevate, support it with customer proof, and make it more prominent "
            "on your homepage, Google profile, and follow-up messaging."
        )

    if decision == "defend_position":
        return (
            "Keep review requests consistent, watch the closest competitor below you, and reinforce the reasons "
            "customers already choose you."
        )

    if decision == "win_positioning":
        return (
            "Elevate one clear advantage and make it the dominant message across your website and reviews."
            "across website copy, Google posts, and review responses."
        )

    return str(item.get("how_to_implement") or item.get("detail") or "").strip()


def _is_weak_insight(item: dict) -> bool:
    text = " ".join([
        str(item.get("summary") or ""),
        str(item.get("action") or ""),
        str(item.get("why_it_matters") or ""),
        str(item.get("how_to_implement") or ""),
    ]).lower()

    weak_patterns = [
        "use this as strategic context",
        "use this as context",
        "not as a standalone move",
        "not every movement matters",
        "inform a decision",
        "this signal helps",
        "wait for a clearer opening",
    ]

    return any(pattern in text for pattern in weak_patterns)

def build_client_facing_insights(
    insights: List[Insight],
    previous_insights: Optional[List[Insight]] = None,
    sections: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized: List[Insight] = []
    for i in insights:
        if not isinstance(i, dict):
            continue
        try:
            item = _normalize_insight(i)
        except Exception:
            continue
        if isinstance(item, dict):
            normalized.append(item)

    cleaned: List[Insight] = []
    for item in normalized:
        summary = str(item.get("summary") or "").strip()
        if not summary:
            continue
        if summary.lower() == "none":
            continue
        cleaned.append(item)

    seen: set[str] = set()
    seen_hidden_themes: set[tuple[str, ...]] = set()
    summary_deduped: List[Insight] = []

    for item in cleaned:
        key = str(item.get("summary") or "").lower().strip()
        if key in seen:
            continue

        insight_type = str(item.get("type") or "").strip().lower()
        if insight_type == "hidden_opportunity":
            owner_themes = _hidden_opportunity_owner_themes(item)
            if owner_themes:
                if owner_themes in seen_hidden_themes:
                    continue
                seen_hidden_themes.add(owner_themes)

        seen.add(key)
        summary_deduped.append(item)

    reduced = _dedupe_and_reduce(summary_deduped)
    compressed = _apply_compression_layer(reduced)
    compressed = _suppress_repetitive_actions(compressed)
    compressed = _suppress_stale_repeated_insights(
        compressed,
        previous_insights=previous_insights,
    )
    ordered = _sort_insights(compressed)
    grouped_sections = _group_sections(ordered)

    summary_text = _build_executive_summary(ordered)


    action_plan = {
        "immediate": [
            _strip_internal_fields(i)
            for i in ordered
            if str(i.get("priority") or "").strip() == "Immediate"
        ],
        "next": [
            _strip_internal_fields(i)
            for i in ordered
            if str(i.get("priority") or "").strip() == "Next"
        ],
        "monitor": [
            _strip_internal_fields(i)
            for i in ordered
            if str(i.get("priority") or "").strip() == "Monitor"
        ],
    }

    this_month_focus = build_this_month_focus(action_plan, sections=sections)

    # -------------------------
    # Review target recommendation
    # -------------------------

    review_target = None
    review_pace = None
    review_overtake = None

    try:
        sections = sections or {}

        sov = sections.get("share_of_voice") or {}
        sov_rows = sov.get("rows") or []

        owner_review_count = None

        for row in sov_rows:
            if row.get("is_business"):
                owner_review_count = (
                    row.get("review_count")
                    or row.get("google_review_count")
                    or row.get("reviews_total")
                )
                break

        review_target = build_review_target_recommendation(
            owner_review_count=owner_review_count,
            competitor_rows=sov_rows,
        )
    except Exception:
        review_target = None

    try:
        review_pulse = sections.get("review_pulse") or {}
        pulse_rows = review_pulse.get("rows") or review_pulse.get("data") or []

        owner_review_delta_30d = None
        next_competitor_delta_30d = None
        next_competitor_name = review_target.get("next_competitor") if review_target else None

        for row in pulse_rows:
            name = str(row.get("competitor_name") or row.get("name") or "").strip()
            is_owner = bool(row.get("is_business"))

            delta = (
                row.get("review_delta_30d")
                or row.get("delta_30d")
                or row.get("net_review_change")
                or row.get("review_gain")
            )

            if is_owner:
                owner_review_delta_30d = delta

            if next_competitor_name and name == next_competitor_name:
                next_competitor_delta_30d = delta

        review_pace = build_review_pace_message(
            review_target=review_target,
            owner_review_delta_30d=owner_review_delta_30d,
        )

        review_overtake = build_review_overtake_projection(
            review_target=review_target,
            owner_review_delta_30d=owner_review_delta_30d,
            next_competitor_delta_30d=next_competitor_delta_30d,
        )
    except Exception:
        review_pace = None
        review_overtake = None

    # -------------------------
    # Review pace vs target
    # -------------------------

    review_pace = None

    try:
        owner_review_delta_30d = None

        for i in insights:
            if isinstance(i, dict) and i.get("type") in {"review_pulse", "momentum", "velocity_trends"}:
                details = i.get("details") or {}
                owner_review_delta_30d = (
                    details.get("owner_review_delta_30d")
                    or details.get("owner_delta_30d")
                    or details.get("review_delta_30d")
                    or details.get("owner_review_gain")
                    or details.get("review_gain")
                )

        review_pace = build_review_pace_message(
            review_target=review_target,
            owner_review_delta_30d=owner_review_delta_30d,
        )
    except Exception:
        review_pace = None

    # -------------------------
    # Overtake projection
    # -------------------------

    review_overtake = None

    try:
        owner_review_delta_30d = None
        next_competitor_delta_30d = None
        next_competitor_name = None

        if review_target:
            next_competitor_name = review_target.get("next_competitor")

        for i in insights:
            if isinstance(i, dict) and i.get("type") in {"review_pulse", "momentum", "velocity_trends"}:
                details = i.get("details") or {}

                owner_review_delta_30d = (
                    details.get("owner_review_delta_30d")
                    or details.get("owner_delta_30d")
                    or details.get("review_delta_30d")
                    or details.get("owner_review_gain")
                    or details.get("review_gain")
                )

                competitor_deltas = (
                    details.get("competitor_deltas")
                    or details.get("competitor_review_deltas")
                    or details.get("rows")
                    or []
                )

                if next_competitor_name and isinstance(competitor_deltas, list):
                    for row in competitor_deltas:
                        row_name = str(
                            row.get("competitor_name")
                            or row.get("name")
                            or ""
                        ).strip()

                        if row_name == next_competitor_name:
                            next_competitor_delta_30d = (
                                row.get("review_delta_30d")
                                or row.get("delta_30d")
                                or row.get("review_gain")
                                or row.get("net_review_change")
                            )
                            break

        review_overtake = build_review_overtake_projection(
            review_target=review_target,
            owner_review_delta_30d=owner_review_delta_30d,
            next_competitor_delta_30d=next_competitor_delta_30d,
        )
    except Exception:
        review_overtake = None

    return {
        "flat_insights": ordered,
        "grouped_sections": grouped_sections,
        "summary_text": summary_text,
        "action_plan": action_plan,
        "this_month_focus": this_month_focus,
        "review_target": review_target,
        "review_pace": review_pace,
        "review_overtake": review_overtake,
    }


def _normalize_theme_list(value: Any) -> List[str]:
    if isinstance(value, list):
        out: List[str] = []
        for v in value:
            text = str(v or "").strip().lower()
            if text:
                out.append(text)
        return out

    text = str(value or "").strip().lower()
    if not text:
        return []

    parts = re.split(r",|/| and ", text)
    return [p.strip() for p in parts if p.strip()]


def _hidden_opportunity_owner_themes(item: Insight) -> Tuple[str, ...]:
    details = item.get("details") or {}

    candidates = (
        details.get("owner_winning_themes"),
        details.get("owner_themes"),
        details.get("winning_themes"),
        details.get("owner_theme"),
        details.get("theme"),
        details.get("primary_theme"),
    )

    themes: List[str] = []
    for candidate in candidates:
        themes = _normalize_theme_list(candidate)
        if themes:
            break

    if not themes:
        counts = details.get("owner_praise_theme_counts") or {}
        if isinstance(counts, dict):
            ranked = sorted(
                [
                    (str(theme).strip().lower(), int(count or 0))
                    for theme, count in counts.items()
                    if str(theme).strip() and int(count or 0) > 0
                ],
                key=lambda x: (-x[1], x[0]),
            )
            themes = [theme for theme, _ in ranked[:3]]

    if not themes:
        summary = str(item.get("summary") or "").lower()

        if "you are winning on " in summary:
            after = summary.split("you are winning on ", 1)[1]
            before_break = after.split(".", 1)[0]
            before_break = before_break.split(", while ", 1)[0]
            before_break = before_break.split(". reposition", 1)[0]
            themes = _normalize_theme_list(before_break)

        elif "outperforming on " in summary:
            after = summary.split("outperforming on ", 1)[1]
            before_break = after.split(", while ", 1)[0]
            before_break = before_break.split(". reposition", 1)[0]
            themes = _normalize_theme_list(before_break)

    return tuple(sorted(set(themes)))


def _hidden_opportunity_specificity_score(item: Insight) -> Tuple[int, int, int]:
    details = item.get("details") or {}
    summary = str(item.get("summary") or "")
    competitor_name = _competitor_from_details(details)

    raw_competitor_themes = (
        details.get("competitor_winning_themes")
        or details.get("competitor_themes")
        or details.get("losing_themes")
        or details.get("competitor_theme")
        or details.get("losing_theme")
        or []
    )
    competitor_themes = _normalize_theme_list(raw_competitor_themes)

    if not competitor_themes:
        summary_lower = summary.lower()
        if "while" in summary_lower and "winning on" in summary_lower:
            try:
                after = summary_lower.split("winning on", 1)[1]
                extracted = after.split(".")[0]
                competitor_themes = _normalize_theme_list(extracted)
            except Exception:
                competitor_themes = []

    return (
        1 if competitor_name else 0,
        len(competitor_themes),
        len(summary),
    )

def _derive_decision(item: dict) -> str:
    text = " ".join([
        str(item.get("summary") or ""),
        str(item.get("type") or ""),
        str(item.get("section") or ""),
    ]).lower()

    if "messaging" in text or "language" in text:
        return "improve_conversion"

    if "behind" in text or "gap" in text or "trail" in text:
        return "close_gap"

    if "leader" in text or "controls" in text:
        return "challenge_leader"

    if "upper tier" in text or "ranked #3" in text:
        return "push_for_top"

    if "pressure" in text or "ahead by" in text:
        return "defend_position"

    if any(x in text for x in ["trust", "speed", "quality", "convenience"]):
        return "win_positioning"

    return "general"


def _build_strategic_action(item: dict) -> str:
    d = _derive_decision(item)

    if d == "improve_conversion":
        return "Update messaging to reflect how customers describe their experience to improve conversion."

    if d == "close_gap":
        return "Increase review velocity to close the gap with higher-ranked competitors."

    if d == "challenge_leader":
        return "Win customers from the market leader by highlighting clear differentiators and clear reasons customers should choose you."

    if d == "push_for_top":
        return "Use your position to push more aggressively toward the top spot."

    if d == "defend_position":
        return "Protect your current position by maintaining consistent review growth."

    if d == "win_positioning":
        return "Own one customer value clearly and reinforce it across all messaging."

    return str(item.get("action") or item.get("summary") or "")


def _build_execution_steps(item: dict) -> str:
    d = _derive_decision(item)

    if d == "improve_conversion":
        return "Update homepage, service pages, and testimonials to reflect customer language."

    if d == "close_gap":
        return "Set a monthly review target and request reviews after every job."

    if d == "challenge_leader":
        return "Create a direct comparison message and highlight proof points."

    if d == "push_for_top":
        return "Elevate your strongest differentiator across all buyer touchpoints."

    if d == "defend_position":
        return "Monitor nearby competitors and maintain steady review growth."

    if d == "win_positioning":
        return "Select one value and make it the core of your messaging."

    return str(item.get("how_to_implement") or "")

def _normalize_insight(insight: Insight) -> Insight:
    item = dict(insight or {})

    insight_type = str(item.get("type") or "").strip()
    severity = str(item.get("severity") or "info").strip().lower()
    raw_summary = str(item.get("summary") or item.get("message") or "").strip()
    details = item.get("details") or {}
    if not isinstance(details, dict):
        details = {}

    section = _infer_section(insight_type, raw_summary)
    priority = _infer_priority(insight_type, severity, raw_summary, details, item)
    sharp_summary = _rewrite_summary(insight_type, raw_summary, details)

    # -----------------------------------------
    # Tighten summary phrasing
    # -----------------------------------------
    if sharp_summary:
        s = sharp_summary.strip()
        s_lower = s.lower()

        if "messaging does not fully reflect" in s_lower:
            sharp_summary = "Messaging is not aligned with how customers describe value."

        elif "positioning opening" in s_lower:
            sharp_summary = "Clear positioning opportunity: emphasize speed and convenience."

        elif "reducing positioning clarity" in s_lower:
            sharp_summary = s.replace("reducing positioning clarity", "").strip().rstrip(".") + "."

        elif "complaints are limited" in s_lower:
            sharp_summary = (
                "No meaningful customer friction signals detected. "
                "Scheduling appears occasionally but is not yet a consistent issue."
            )

    default_action, default_why_it_matters, default_how_to_implement = _build_action_layer(
        insight_type=insight_type,
        summary=sharp_summary,
        details=details,
        priority=priority,
        section=section,
    )

    # -------------------------
    # UPGRADED STRATEGY LAYER
    # -------------------------
    decision = _derive_decision({
        "summary": sharp_summary,
        "type": insight_type,
        "section": section,
        "details": details,
    })

    strategic_action = _build_strategic_action({
        "summary": sharp_summary,
        "type": insight_type,
        "section": section,
        "details": details,
    })

    strategic_how = _build_execution_steps({
        "summary": sharp_summary,
        "type": insight_type,
        "section": section,
        "details": details,
    })

    implication = _clean_sentence(str(item.get("implication") or "").strip())
    recommended_action = _clean_sentence(str(item.get("recommended_action") or "").strip())
    explicit_how = _clean_sentence(str(item.get("how_to_implement") or "").strip())
    explicit_why = _clean_sentence(str(item.get("why_it_matters") or "").strip())

    item["section"] = section
    item["priority"] = priority
    item["summary"] = sharp_summary

    if insight_type in {
        "hidden_opportunity",
        "challenger_gap",
        "market_dominance",
        "baseline_rank",
        "market_position",
    }:
        item["action"] = strategic_action
        item["why_it_matters"] = default_why_it_matters
        item["how_to_implement"] = strategic_how
        item["decision"] = decision

        resolved_how = default_how_to_implement
        if _is_generic_implementation(resolved_how):
            resolved_how = ""

        item["how_to_implement"] = resolved_how
    else:
        item["action"] = recommended_action or default_action
        item["why_it_matters"] = explicit_why or implication or default_why_it_matters

        resolved_how = explicit_how or default_how_to_implement
        if _is_generic_implementation(resolved_how):
            resolved_how = ""

        item["how_to_implement"] = resolved_how

    item["display_order"] = _display_order(section, priority)

    # -------------------------
    # FORCE STRATEGIC ACTIONS (ALL INSIGHTS)
    # -------------------------
    item["action"] = strategic_action
    item["how_to_implement"] = strategic_how
    item["decision"] = decision

    item["_dedupe_key"] = _build_dedupe_key(item)

    return item


def _apply_compression_layer(items: List[Insight]) -> List[Insight]:
    items = list(items)

    praise_items = [i for i in items if str(i.get("type") or "") == "praise_themes"]
    complaint_items = [i for i in items if str(i.get("type") or "") == "complaint_themes"]
    messaging_items = [i for i in items if str(i.get("type") or "") == "messaging_mismatch"]

    rollups: List[Insight] = []

    perception_rollup = _build_praise_rollup(praise_items)
    if perception_rollup:
        rollups.append(_normalize_insight(perception_rollup))

    complaint_rollup = _build_complaint_rollup(complaint_items)
    if complaint_rollup:
        rollups.append(_normalize_insight(complaint_rollup))

    messaging_rollup = _build_messaging_rollup(messaging_items)
    if messaging_rollup:
        rollups.append(_normalize_insight(messaging_rollup))

    kept_items: List[Insight] = []

    top_messaging_examples = _select_top_messaging_examples(messaging_items, limit=0)
    top_messaging_keys = {_stable_item_key(i) for i in top_messaging_examples}

    for item in items:
        t = str(item.get("type") or "")
        if t == "praise_themes":
            continue
        if t == "complaint_themes":
            continue
        if t == "messaging_mismatch":
            if _stable_item_key(item) not in top_messaging_keys:
                continue
        kept_items.append(item)

    kept_items.extend(rollups)
    kept_items = _dedupe_and_reduce(kept_items)

    return kept_items


def _build_praise_rollup(items: List[Insight]) -> Optional[Insight]:
    if not items:
        return None

    theme_counter: Counter[str] = Counter()
    competitor_theme_map: Dict[str, set[str]] = defaultdict(set)
    competitor_review_counts: Dict[str, int] = {}
    owner_name = None
    owner_themes: set[str] = set()

    for item in items:
        details = item.get("details") or {}
        competitor_name = str(details.get("competitor_name") or "A competitor").strip()
        review_count = int(details.get("review_count") or 0)

        if competitor_name:
            competitor_review_counts[competitor_name] = max(
                competitor_review_counts.get(competitor_name, 0),
                review_count,
            )

        counts = details.get("praise_theme_counts") or {}
        if isinstance(counts, dict):
            for theme, count in counts.items():
                try:
                    n = int(count or 0)
                except Exception:
                    n = 0
                if n > 0:
                    theme_counter[theme] += n
                    competitor_theme_map[competitor_name].add(theme)

        if details.get("is_owner") is True:
            owner_name = competitor_name
            owner_themes |= set(competitor_theme_map.get(competitor_name, set()))

    if not theme_counter:
        return None

    top_themes = [t for t, _ in theme_counter.most_common(3)]
    top_competitors = sorted(
        competitor_theme_map.keys(),
        key=lambda name: (
            -len(competitor_theme_map.get(name, set())),
            -competitor_review_counts.get(name, 0),
            name.lower(),
        ),
    )[:3]

    theme_phrase = _human_join(top_themes)
    competitor_phrase = _human_join(top_competitors)

    if owner_name and owner_themes:
        owner_top = sorted(
            owner_themes,
            key=lambda x: (-theme_counter.get(x, 0), x),
        )[:2]
        owner_phrase = _human_join(owner_top)
        summary = (
            f"Customer perception centers on {theme_phrase}. "
            f"{owner_name} appears strongest on {owner_phrase}, while competitors most visibly win on {theme_phrase}."
        )
    else:
        summary = (
            f"Customer perception is led by {theme_phrase}. "
            f"The most visible competitors on these strengths are {competitor_phrase}."
        )

    return {
        "type": "perception_rollup",
        "severity": "info",
        "summary": summary,
        "details": {
            "top_themes": top_themes,
            "top_competitors": top_competitors,
            "theme_counts": dict(theme_counter),
            "source_count": len(items),
        },
    }


def _build_complaint_rollup(items: List[Insight]) -> Optional[Insight]:
    if not items:
        return None

    theme_counter: Counter[str] = Counter()
    competitor_theme_map: Dict[str, set[str]] = defaultdict(set)
    competitor_review_counts: Dict[str, int] = {}

    for item in items:
        details = item.get("details") or {}
        competitor_name = str(details.get("competitor_name") or "A competitor").strip()
        review_count = int(details.get("review_count") or 0)

        if competitor_name:
            competitor_review_counts[competitor_name] = max(
                competitor_review_counts.get(competitor_name, 0),
                review_count,
            )

        counts = details.get("complaint_theme_counts") or {}
        if isinstance(counts, dict):
            for theme, count in counts.items():
                try:
                    n = int(count or 0)
                except Exception:
                    n = 0
                if n > 0:
                    theme_counter[theme] += n
                    competitor_theme_map[competitor_name].add(theme)

    if not theme_counter:
        return None

    top_themes = [t for t, _ in theme_counter.most_common(3)]
    exposed_competitors = sorted(
        competitor_theme_map.keys(),
        key=lambda name: (
            -len(competitor_theme_map.get(name, set())),
            -competitor_review_counts.get(name, 0),
            name.lower(),
        ),
    )[:3]

    summary = (
        f"Competitor complaints cluster around {_human_join(top_themes)}. "
        f"The clearest exposed competitors are {_human_join(exposed_competitors)}."
    )

    return {
        "type": "complaint_rollup",
        "severity": "info",
        "summary": summary,
        "details": {
            "top_themes": top_themes,
            "exposed_competitors": exposed_competitors,
            "theme_counts": dict(theme_counter),
            "source_count": len(items),
        },
    }


def _build_messaging_rollup(items: List[Insight]) -> Optional[Insight]:
    if not items:
        return None

    praised_theme_counter: Counter[str] = Counter()
    gap_counter: Counter[str] = Counter()
    affected_competitors: List[str] = []

    for item in items:
        details = item.get("details") or {}
        competitor_name = str(details.get("competitor_name") or "").strip()
        if competitor_name:
            affected_competitors.append(competitor_name)

        praised = details.get("praised_themes") or details.get("praise_themes") or []
        if isinstance(praised, list):
            for theme in praised:
                if str(theme).strip():
                    praised_theme_counter[str(theme).strip()] += 1

        missing = (
            details.get("missing_themes")
            or details.get("gap_themes")
            or details.get("underrepresented_themes")
            or []
        )
        if isinstance(missing, list):
            for theme in missing:
                if str(theme).strip():
                    gap_counter[str(theme).strip()] += 1

    top_praised = [t for t, _ in praised_theme_counter.most_common(3)]
    top_gaps = [t for t, _ in gap_counter.most_common(3)]
    competitor_list = _human_join(sorted(set(affected_competitors))[:3])

    if top_praised and top_gaps:
        summary = (
            f"Customer language consistently emphasizes {_human_join(top_praised)}, "
            f"but visible messaging under-emphasizes {_human_join(top_gaps)}. "
            f"This creates a positioning gap across the market."
        )
    elif top_praised:
        summary = (
            f"Customers consistently describe value in terms of {_human_join(top_praised)}, "
            f"but this is not clearly reflected in most competitor messaging."
        )
    else:
        summary = (
            "Messaging does not fully reflect how customers describe value, reducing positioning clarity."
        )

    return {
        "type": "messaging_rollup",
        "severity": "warning",
        "summary": summary,
        "details": {
            "top_praised_themes": top_praised,
            "top_gap_themes": top_gaps,
            "affected_competitors": sorted(set(affected_competitors)),
            "source_count": len(items),
            "sample_competitors": competitor_list,
        },
    }


def _select_top_messaging_examples(items: List[Insight], limit: int = 1) -> List[Insight]:
    if not items:
        return []

    def score(item: Insight) -> Tuple[int, int, str]:
        details = item.get("details") or {}
        praised = details.get("praised_themes") or details.get("praise_themes") or []
        missing = (
            details.get("missing_themes")
            or details.get("gap_themes")
            or details.get("underrepresented_themes")
            or []
        )

        praised_count = len(praised) if isinstance(praised, list) else 0
        missing_count = len(missing) if isinstance(missing, list) else 0
        summary = str(item.get("summary") or "")
        return (praised_count + missing_count, len(summary), summary.lower())

    ranked = sorted(items, key=score, reverse=True)
    return ranked[:limit]


def _stable_item_key(item: Insight) -> str:
    details = item.get("details") or {}
    return "|".join(
        [
            str(item.get("type") or ""),
            str(details.get("competitor_id") or ""),
            str(details.get("competitor_name") or ""),
            str(item.get("summary") or ""),
        ]
    )


def _infer_section(insight_type: str, summary: str) -> str:
    if insight_type in TYPE_TO_SECTION:
        return TYPE_TO_SECTION[insight_type]

    s = summary.lower()

    if "message" in s or "position" in s or "homepage" in s or "copy" in s:
        return SECTION_MESSAGING
    if "customer" in s or "review" in s or "praise" in s or "complaint" in s:
        return SECTION_PERCEPTION
    if "threat" in s or "pressure" in s or "surge" in s or "pulling away" in s:
        return SECTION_THREATS
    if "opportunity" in s or "winning on" in s:
        return SECTION_POSITIONING
    if "rank" in s or "share" in s or "market" in s or "velocity" in s:
        return SECTION_MARKET

    return SECTION_OTHER


def _infer_priority(
    insight_type: str,
    severity: str,
    summary: str,
    details: Dict[str, Any],
    item: Insight,
) -> str:
    raw_priority = str(item.get("priority") or "").strip().lower()
    if raw_priority in RAW_PRIORITY_TO_DISPLAY:
        return RAW_PRIORITY_TO_DISPLAY[raw_priority]

    if insight_type in TYPE_PRIORITY_HINT:
        return TYPE_PRIORITY_HINT[insight_type]

    if severity in SEVERITY_TO_PRIORITY:
        return SEVERITY_TO_PRIORITY[severity]

    s = summary.lower()

    if any(
        phrase in s
        for phrase in [
            "losing",
            "gap",
            "surge",
            "pulling away",
            "messaging gap",
            "messaging is misaligned",
            "hidden opportunity",
            "threat",
            "pressure",
        ]
    ):
        return "Immediate"

    if any(
        phrase in s
        for phrase in [
            "customers consistently praise",
            "customers repeatedly mention",
            "position",
            "rank",
            "share",
            "customer perception",
            "competitor complaints",
        ]
    ):
        return "Next"

    return "Monitor"


def _rewrite_summary(insight_type: str, raw_summary: str, details: Dict[str, Any]) -> str:
    summary = _clean_sentence(raw_summary)

    summary = summary.replace("YOU vs THEM:", "")
    summary = summary.replace("YOU vs THEM", "")

    summary = summary.replace(" is ahead by ", " leads by ")
    summary = summary.replace(" is behind by ", " trails by ")
    summary = summary.replace(" ahead by ", " leads by ")
    summary = summary.replace(" behind by ", " trails by ")
    summary = summary.strip()

    if not summary:
        return summary

    if insight_type == "praise_themes":
        return _tighten_praise_summary(summary)

    if insight_type == "complaint_themes":
        return _tighten_complaint_summary(summary)

    if insight_type == "hidden_opportunity":
        return _tighten_hidden_opportunity_summary(summary)

    if insight_type == "messaging_mismatch":
        return _tighten_messaging_gap_summary(summary)

    if insight_type == "market_quiet":
        return summary.replace(
            "Market was mostly flat versus the prior report.",
            "Competitive movement was limited this period.",
        )

    if insight_type == "position_change":
        return summary.replace("Position shift:", "").strip()

    if insight_type == "market_movers":
        return summary

    if insight_type == "market_concentration":
        return _tighten_market_concentration_summary(summary)

    if insight_type in {"perception_rollup", "complaint_rollup", "messaging_rollup"}:
        return _clean_sentence(summary)

    return summary


def _tighten_praise_summary(summary: str) -> str:
    s = summary
    s = s.replace("Customers consistently praise ", "")
    s = s.replace("Customers repeatedly praise ", "")
    s = s.replace("Customers praise ", "")

    if s:
        return f"Core strength: {s[0].upper()}{s[1:]}"
    return summary


def _tighten_complaint_summary(summary: str) -> str:
    s = summary
    s = s.replace("Customers consistently complain about ", "")
    s = s.replace("Customers repeatedly complain about ", "")
    s = s.replace("Customers complain about ", "")

    if s:
        return f"Customer friction: {s[0].upper()}{s[1:]}"
    return summary


def _tighten_hidden_opportunity_summary(summary: str) -> str:
    s = summary
    s = s.replace("You are winning on ", "Positioning opening: you are outperforming on ")
    s = s.replace("competitors winning on", "competitors are more visible on")
    return _clean_sentence(s)


def _tighten_messaging_gap_summary(summary: str) -> str:
    s = summary
    s = s.replace("Messaging gap detected", "Messaging disconnect")
    s = s.replace("website language", "site messaging")
    s = s.replace("review language", "customer language")
    s = s.replace("customer customer language", "customer language")
    return _clean_sentence(s)


def _tighten_market_concentration_summary(summary: str) -> str:
    s = summary.replace("YOU vs THEM:", "")
    s = s.strip()
    if not s:
        return "Review share is concentrated at the top of the market."
    if s and s[0].islower():
        s = s[0].upper() + s[1:]
    if "The top 2 competitors control" in s:
        s = s.replace(
            "The top 2 competitors control",
            "Review share is concentrated: the top 2 competitors control",
        )
    return s


def _first_nonempty_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _theme_from_details(details: Dict[str, Any]) -> str:
    candidates = [
        details.get("theme"),
        details.get("owner_theme"),
        details.get("competitor_theme"),
        details.get("theme_phrase"),
        details.get("primary_theme"),
    ]
    text = _first_nonempty_text(*candidates)
    return text.lower() if text else ""


def _competitor_from_details(details: Dict[str, Any]) -> str:
    return _first_nonempty_text(
        details.get("competitor_name"),
        details.get("leader_name"),
        details.get("challenger_name"),
    )


def _owner_theme_phrase(details: Dict[str, Any]) -> str:
    themes = (
        details.get("owner_winning_themes")
        or details.get("owner_themes")
        or details.get("winning_themes")
        or details.get("owner_theme")
        or details.get("theme")
        or details.get("primary_theme")
        or []
    )

    normalized = _normalize_theme_list(themes)
    if not normalized:
        return ""

    return _human_join(normalized[:3])


def _themes_to_phrase(themes: List[str]) -> str:
    cleaned = [str(t).strip().lower() for t in themes if str(t).strip()]
    if not cleaned:
        return ""
    return _human_join(cleaned[:3])


def _build_action_layer(
    insight_type: str,
    summary: str,
    details: Dict[str, Any],
    priority: str,
    section: str,
) -> Tuple[str, str, str]:
    theme = _theme_from_details(details)
    competitor_name = _competitor_from_details(details)
    owner_is_leader = bool(details.get("owner_is_leader"))
    owner_is_challenger = bool(details.get("owner_is_challenger"))

    if insight_type == "praise_themes":
        if theme:
            return (
                f"Promote your strength on {theme} more aggressively in headlines, proof points, and sales conversations.",
                f"If customers already value your {theme}, surfacing it more clearly can increase conversion without changing the service itself.",
                f"Add {theme} to homepage copy, service pages, proposal language, and testimonial pull-quotes. Use the same customer-facing wording consistently.",
            )
        return (
            "Promote this strength more aggressively in headlines, proof points, and sales conversations.",
            "If customers already value this, surfacing it more clearly can increase conversion without changing the service itself.",
            "Add the theme to homepage copy, service pages, proposal language, and before/after proof. Use the same customer-facing wording consistently.",
        )

    if insight_type == "complaint_themes":
        if theme:
            return (
                f"Reduce friction around {theme} operationally, then address it directly in messaging once performance improves.",
                f"Repeated complaints about {theme} suppress referrals, hurt review velocity, and create an opening for competitors to position against you.",
                f"Identify the root cause behind the {theme} complaints, assign an owner, and publish one visible trust-building message showing the issue is being addressed.",
            )
        return (
            "Reduce this friction operationally, then address it directly in messaging once performance improves.",
            "Repeated complaints suppress referrals, hurt review velocity, and create an opening for competitors to position against you.",
            "Identify the operational root cause, assign an owner, and create one visible trust-building message that shows the issue is being handled.",
        )

    if insight_type == "perception_rollup":
        return (
            "Lead with the strongest repeated customer-value themes in your positioning.",
            "The report should highlight the market story, not just a list of individual competitor compliments.",
            "Choose one primary theme for homepage positioning, one supporting proof theme, and one competitor contrast to reinforce in sales conversations.",
        )

    if insight_type == "complaint_rollup":
        return (
            "Exploit these competitor weaknesses with clear contrast messaging and proof.",
            "Recurring complaint patterns create openings to differentiate without changing your offer.",
            "Create one comparison statement, one testimonial or proof point, and one service-page message that directly counters the most repeated complaint themes.",
        )

    if insight_type == "messaging_rollup":
        return (
            "Realign messaging to match how customers actually describe value.",
            "When messaging reflects how customers actually describe value, conversion improves.",
            "Update homepage, service pages, and testimonials so the same themes appear consistently across all buyer touchpoints.",
        )

    if insight_type == "hidden_opportunity":
        owner_themes = _normalize_theme_list(
            details.get("owner_winning_themes")
            or details.get("owner_themes")
            or details.get("winning_themes")
            or details.get("theme")
            or details.get("primary_theme")
        )

        competitor_themes = _normalize_theme_list(
            details.get("competitor_winning_themes")
            or details.get("competitor_themes")
            or details.get("losing_themes")
            or details.get("competitor_theme")
            or details.get("losing_theme")
        )

        owner_phrase = _themes_to_phrase(owner_themes)
        competitor_phrase = _themes_to_phrase(competitor_themes)
        competitor_name = str(details.get("competitor_name") or "").strip()

        owner_theme_set = set(owner_themes)

        if owner_phrase and competitor_name and competitor_phrase:
            if "convenience" in owner_theme_set and "speed" not in owner_theme_set:
                return (
                    f"Own convenience more explicitly against {competitor_name}.",
                    f"If buyers see you as the easier choice while {competitor_name} owns {competitor_phrase}, you can win more decisions without needing to beat them on every dimension.",
                    "Add faster scheduling, easier access, simpler visit flow, and lower-friction experience proof to homepage copy, booking touchpoints, and front desk scripting.",
                )

            if "speed" in owner_theme_set:
                return (
                    f"Position speed and convenience as your clearest contrast against {competitor_name}.",
                    f"When {competitor_name} is stronger on {competitor_phrase}, visible speed and convenience can become the deciding reason buyers choose you instead.",
                    "This week: update 1 homepage headline to emphasize speed and convenience. Add 1 proof point showing faster service. Update 1 follow-up message or booking touchpoint to reinforce the same position.",
                )

            if "trust" in owner_theme_set or "communication" in owner_theme_set:
                return (
                    f"Use trust and clarity to separate from {competitor_name}.",
                    f"If you can own {owner_phrase} more clearly while {competitor_name} wins on {competitor_phrase}, you improve preference at the moment buyers are comparing options.",
                    "Strengthen doctor credibility, testimonial proof, and explanation-driven messaging across service pages, consultation language, and review requests.",
                )

            if "professionalism" in owner_theme_set:
                return (
                    f"Turn professionalism into a sharper competitive contrast against {competitor_name}.",
                    f"Professionalism can elevate perceived quality even when {competitor_name} remains stronger on {competitor_phrase}.",
                    "Showcase team expertise, process consistency, and polished patient experience through testimonial selection, staff bios, and service-page proof.",
                )

            return (
                f"Make your edge on {owner_phrase} the clearest contrast against {competitor_name}.",
                f"If buyers more quickly associate you with {owner_phrase}, you can win preference even when competitors remain stronger on {competitor_phrase}.",
                f"Build one homepage proof block, one comparison message, and one repeated sales statement around {owner_phrase}.",
            )

        if owner_phrase:
            if "speed" in owner_theme_set or "convenience" in owner_theme_set:
                return (
                    f"Own the market position around {owner_phrase}.",
                    "Speed and convenience drive patient choice when clearly communicated."
                    "Emphasize fast scheduling, short wait times, and ease of experience across homepage, booking flow, and front desk scripting.",
                )

            if "trust" in owner_theme_set or "communication" in owner_theme_set:
                return (
                    f"Build stronger positioning around {owner_phrase}.",
                    "Trust and communication drive patient confidence and long-term retention.",
                    "Highlight testimonials, doctor credibility, and clear explanations in service pages and consultations.",
                )

            if "professionalism" in owner_theme_set:
                return (
                    f"Turn {owner_phrase} into a defining brand signal.",
                    "Professionalism reinforces perceived quality and reliability.",
                    "Showcase staff expertise, process clarity, and consistent patient experience across all touchpoints.",
                )

            return (
                f"Turn your advantage on {owner_phrase} into a visible position.",
                "Strength only matters if it is clearly communicated to buyers.",
                f"Build messaging, proof, and sales language consistently around {owner_phrase}.",
            )

        return (
            "Turn this into a visible market position.",
            "Unclear positioning reduces conversion even if the product is strong.",
            "Clarify messaging, proof, and differentiation in customer-facing materials.",
        )

    if insight_type == "messaging_mismatch":
        if theme:
            return (
                f"Align site language with how customers naturally describe {theme}.",
                f"When your messaging does not match customer language around {theme}, buyers work harder to understand why they should choose you.",
                f"Rewrite hero copy, service page headers, and testimonial captions using the same phrases customers repeat when describing {theme}.",
            )
        return (
            "Align site language with how customers naturally describe value.",
            "When your messaging does not match customer language, buyers work harder to understand why they should choose you.",
            "Rewrite hero copy, service page headers, and testimonial captions using the same phrases customers repeat in reviews.",
        )

    if insight_type == "challenger_gap":
        if owner_is_leader:
            return (
                "Protect your lead by reinforcing the reasons buyers already choose you.",
                "A review lead matters most when it stays connected to visible trust, proof, and buyer preference.",
                "Double down on your strongest proof points, keep review generation consistent, and make your lead visible in web copy and sales language.",
            )
        if owner_is_challenger:
            return (
                "Close the gap with a tighter positioning claim and stronger review acquisition discipline.",
                "When you are close to the leader, a focused push can change buyer perception faster than broad changes.",
                "Choose one differentiator to emphasize, ask for more reviews from your happiest customers, and track whether the gap narrows each cycle.",
            )
        return (
            "Monitor the leader-challenger gap and decide whether it changes the competitive story.",
            "Large gaps can shape buyer expectations even before prospects compare providers directly.",
            "Review whether the leader is winning on volume, visibility, or positioning, then decide whether your response should be operational or messaging-driven.",
        )

    if insight_type in {"competitive_tier_pressure", "leader_pulling_away", "competitor_surge"}:
        if competitor_name:
            return (
                f"Respond to {competitor_name} with a focused counter-position rather than broad messaging changes.",
                "A fast-moving competitor can reshape buyer expectations before your team reacts.",
                f"This week: identify what {competitor_name} appears to be gaining on. Defend 1 vulnerable area and publish 1 stronger proof point that supports your position.",
            )
        return (
            "Respond with a focused counter-position rather than broad messaging changes.",
            "A fast-moving competitor can reshape buyer expectations before your team reacts.",
            "Pick one vulnerable area to defend, one advantage to amplify, and one proof point to publish this cycle.",
        )

    if insight_type in {"position_change", "market_movers"}:
        return (
            "Review whether this change is temporary noise or the start of a real shift.",
            "Not every movement matters, but repeated movement usually signals a meaningful change in visibility or buyer preference.",
            "Check the last 2-3 periods, compare review growth and share movement, and decide whether to respond now or keep monitoring.",
        )

    if insight_type == "market_dominance":
        if owner_is_leader:
            return (
                "Use your review-share lead to reinforce why buyers should choose you first.",
                "A leading review share is most valuable when it strengthens trust and makes your advantage feel visible to prospects.",
                "Add a trust-building proof section, keep review requests active, and make your strongest differentiators more explicit in buyer-facing copy.",
            )
        if competitor_name:
            return (
                f"Position more directly against {competitor_name} and narrow the perceived gap.",
                "When one competitor controls outsized review share, buyers may treat them as the default choice unless your difference is easier to understand.",
                f"Create one comparison message against {competitor_name}, strengthen your strongest proof points, and track whether review share begins to rebalance.",
            )
        return (
            "Treat concentrated review share as a signal to sharpen your position.",
            "When one player controls a disproportionate share of reviews, buyer expectations can become anchored around them.",
            "Use clearer proof, stronger differentiation, and steady review generation to reduce that advantage over time.",
        )

    if insight_type == "baseline_rank":
        owner_rank = int(details.get("owner_rank") or 999)

        if owner_is_leader or owner_rank == 1:
            return (
                "Defend your #1 position by making your lead visible and believable.",
                "Rank leadership is easier to lose when the market sees your reviews but not the reasons behind them.",
                "Keep review generation active, strengthen trust signals on key pages, and reinforce the top reasons customers choose you.",
            )
        return (
            "Use your current rank as context for how aggressively to improve your position.",
            "Your current rank shows where momentum needs to convert into stronger preference.",
            "Decide whether the next priority is closing the gap above you, defending against pressure below you, or improving conversion from existing visibility.",
        )

    if insight_type == "market_position":
        tier = str(details.get("tier") or "").strip().lower()
        if owner_is_leader or tier == "leader":
            return (
                "Operate like the market leader by protecting separation, not just describing it.",
                "When you already lead, the right move is usually reinforcing preference and extending distance rather than copying others.",
                "Strengthen your best proof points, maintain review velocity, and make your lead more visible across web copy, testimonials, and follow-up language.",
            )
        if tier == "upper tier":
            return (
                "Use your upper-tier position to challenge more directly for the top spot.",
                "Upper-tier businesses often grow fastest when they sharpen differentiation instead of broadening the message.",
                "Choose one clear advantage to elevate, support it with proof, and make it more prominent across the buyer journey.",
            )
        return (
            "Use your current tier to focus on one realistic improvement at a time.",
            "Lower-tier positioning usually improves faster through clarity and consistency than through broad messaging changes.",
            "Pick one differentiator to emphasize, support it with proof, and build review momentum around that message.",
        )

    if insight_type in {"market_concentration", "share_of_voice", "velocity_trends", "momentum", "top_moves", "market_quiet"}:
        if owner_is_leader:
            return (
                "Use this context to defend your position, not just to describe the market.",
                "Market structure matters most when you are deciding how aggressively to protect a lead versus expand it.",
                "Keep review growth active, reinforce the strongest value claims in buyer-facing copy, and watch for any narrowing from the next closest competitor.",
            )
        return (
            "Use this as strategic context, not as a standalone move.",
            "Market structure helps determine whether you should push offense, defend share, or wait for a clearer opening.",
            "Pair this signal with customer perception and competitor messaging before making a major positioning change.",
        )

    if section == SECTION_POSITIONING:
        if theme:
            return (
                f"Translate this signal into a clearer claim around {theme}.",
                "The fastest growth often comes from better positioning, not only better execution.",
                f"Choose one message about {theme} to elevate and repeat it across web copy, proposals, and follow-up communication.",
            )
        return (
            "Translate this signal into a clearer competitive claim.",
            "The fastest growth often comes from better positioning, not only better execution.",
            "Select one message to elevate and repeat it across web, sales, and follow-up materials.",
        )

    if section == SECTION_THREATS:
        if competitor_name:
            return (
                f"Watch {competitor_name} closely and prepare a specific response.",
                "Threats matter most when they change buyer expectations or reduce your perceived advantage.",
                f"Identify what {competitor_name} is gaining on and tighten your proof, speed, or offer around that area.",
            )
        return (
            "Watch this closely and prepare a specific response.",
            "Threats matter most when they change buyer expectations or reduce your perceived advantage.",
            "Identify what the competitor is gaining on and tighten your proof, speed, or offer around that area.",
        )

    if section == SECTION_MESSAGING:
        if theme:
            return (
                f"Refine customer-facing language to better match buyer priorities around {theme}.",
                "Sharper messaging improves conversion and helps the right value stand out faster.",
                f"Update headlines, proof blocks, and CTAs so {theme} appears clearly and consistently.",
            )
        return (
            "Refine customer-facing language to better match buyer priorities.",
            "Sharper messaging improves conversion and helps the right value stand out faster.",
            "Update headlines, proof blocks, and CTAs around the strongest repeated theme.",
        )

    return (
        "Review this signal and decide whether it should change messaging, operations, or monitoring.",
        "Not every insight needs action, but every insight should inform a decision.",
        "",
    )


def _is_generic_implementation(text: str) -> bool:
    t = str(text or "").strip().lower()
    if not t:
        return True

    generic_phrases = {
        "clarify messaging, proof, and differentiation in customer-facing materials.",
        "review this signal and decide whether it should change messaging, operations, or monitoring.",
    }

    return t in generic_phrases


def _dedupe_and_reduce(items: List[Insight]) -> List[Insight]:
    best_by_key: Dict[str, Insight] = {}

    for item in items:
        key = item.get("_dedupe_key") or _build_dedupe_key(item)
        current = best_by_key.get(key)

        if current is None:
            best_by_key[key] = item
            continue

        if _is_better(item, current):
            best_by_key[key] = item

    reduced = list(best_by_key.values())
    reduced = _suppress_soft_overlap(reduced)
    reduced = _suppress_duplicate_hidden_opportunities(reduced)
    reduced = _suppress_redundant_leader_insights(reduced)
    reduced = _sort_insights(reduced)

    # -----------------------------------------
    # Deduplicate by strategy/action type
    # -----------------------------------------
    seen_actions = set()
    final = []

    for item in reduced:
        action = str(item.get("action") or "").strip().lower()

        if "update messaging" in action:
            action_key = "update_messaging"
        elif "own one customer value" in action:
            action_key = "own_positioning"
        elif "share of voice" in action or "review share" in action:
            action_key = "market_movement"
        elif "review target" in action or "generate reviews" in action:
            action_key = "review_growth"
        else:
            action_key = action

        if action_key in seen_actions:
            continue

        seen_actions.add(action_key)
        final.append(item)

    # -----------------------------------------
    # Ensure at least one market movement insight
    # -----------------------------------------
    has_market = any(
        "share of voice" in (str(i.get("summary") or "").lower())
        or "review share" in (str(i.get("summary") or "").lower())
        for i in final
    )

    if not has_market:
        for item in reduced:
            summary = str(item.get("summary") or "").lower()

            if "share of voice" in summary or "review share" in summary:
                final.append(item)
                break

    return final


def _suppress_soft_overlap(items: List[Insight]) -> List[Insight]:
    strong_types = {"hidden_opportunity", "messaging_mismatch", "messaging_rollup"}
    softer_types = {"weekly_actions", "market_quiet"}

    strong_present = any((i.get("type") or "") in strong_types for i in items)
    if not strong_present:
        return items

    filtered: List[Insight] = []
    for item in items:
        t = item.get("type") or ""
        if t in softer_types and item.get("priority") == "Monitor":
            continue
        filtered.append(item)

    return filtered


def _suppress_duplicate_hidden_opportunities(items: List[Insight]) -> List[Insight]:
    best_by_theme: Dict[Tuple[str, ...], Insight] = {}
    passthrough: List[Insight] = []

    for item in items:
        if str(item.get("type") or "") != "hidden_opportunity":
            passthrough.append(item)
            continue

        owner_themes = _hidden_opportunity_owner_themes(item)
        if not owner_themes:
            passthrough.append(item)
            continue

        current = best_by_theme.get(owner_themes)
        if current is None:
            best_by_theme[owner_themes] = item
            continue

        if _hidden_opportunity_specificity_score(item) > _hidden_opportunity_specificity_score(current):
            best_by_theme[owner_themes] = item

    return passthrough + list(best_by_theme.values())


def _suppress_redundant_leader_insights(items: list[dict]) -> list[dict]:
    """
    Collapse all "leader story" variants into ONE single insight.

    This now handles:
    - review gap ("leading by X reviews")
    - share leadership ("X% of market")
    - rank leadership ("ranked #1")
    - generic leadership ("leading position")

    Keeps ONLY the strongest:
        review gap > share > rank > generic
    """
    if not items:
        return items

    def _text_blob(item: dict) -> str:
        return " ".join([
            str(item.get("title") or ""),
            str(item.get("summary") or ""),
            str(item.get("body") or ""),
            str(item.get("headline") or ""),
        ]).lower()

    def _is_leader_story(item: dict) -> bool:
        text = _text_blob(item)

        return any([
            # existing
            "lead the market" in text,
            "leading the market" in text,
            "currently lead the market" in text,
            "market leader" in text,

            # NEW (this is why yours failed)
            "ranked #1" in text,
            "rank #1" in text,
            "number 1" in text,
            "leading position" in text,
            "hold the leading position" in text,
            "top ranked" in text,
        ])

    def _leader_rank(item: dict) -> int:
        """
        Lower = stronger insight
        """
        text = _text_blob(item)

        # 0 — BEST: review gap
        if "reviews over" in text or "reviews ahead" in text:
            return 0

        # 1 — share leadership
        if "%" in text and "market" in text:
            return 1

        # 2 — rank leadership
        if "ranked #1" in text or "rank #1" in text or "number 1" in text:
            return 2

        # 3 — generic leadership
        if "leading position" in text or "market leader" in text:
            return 3

        return 4

    leader_indexes = [i for i, item in enumerate(items) if _is_leader_story(item)]

    if len(leader_indexes) <= 1:
        return items

    best_index = min(
        leader_indexes,
        key=lambda i: (_leader_rank(items[i]), i)
    )

    result = []
    for i, item in enumerate(items):
        if i == best_index:
            result.append(item)
        elif i in leader_indexes:
            continue
        else:
            result.append(item)

    return result

    def _leader_score(item: Insight) -> Tuple[int, int, int]:
        insight_type = str(item.get("type") or "")
        summary = str(item.get("summary") or "")
        priority = str(item.get("priority") or "")

        type_rank = {
            "market_dominance": 4,
            "share_of_voice": 3,
            "baseline_rank": 2,
            "market_position": 1,
        }

        priority_rank = {
            "Immediate": 3,
            "Next": 2,
            "Monitor": 1,
        }

        return (
            type_rank.get(insight_type, 0),
            priority_rank.get(priority, 0),
            len(summary),
        )

    best_leader = max(leader_items, key=_leader_score)
    return non_leader_items + [best_leader]


def _suppress_repetitive_actions(items: List[Insight]) -> List[Insight]:
    seen_action_families: set[str] = set()
    filtered: List[Insight] = []

    for item in items:
        item = dict(item)

        action = str(item.get("action") or "").strip()
        how = str(item.get("how_to_implement") or "").strip()

        if _is_generic_implementation(how):
            how = ""
            item["how_to_implement"] = ""

        family = _action_family_key(action, how)
        keep = True

        if family and family in seen_action_families:
            item["how_to_implement"] = ""

            rollup_types = {"perception_rollup", "complaint_rollup", "messaging_rollup"}
            if str(item.get("type") or "") in rollup_types:
                keep = False

        if keep:
            filtered.append(item)
            if family:
                seen_action_families.add(family)

    return filtered


def _action_family_key(action: str, how: str) -> str:
    combined = f"{action} {how}".lower()

    families = [
        ("homepage_copy", ["homepage", "hero copy", "service page", "headline", "cta", "testimonial"]),
        ("comparison_message", ["comparison", "contrast", "position against", "counter"]),
        ("review_generation", ["review generation", "review requests", "review velocity"]),
        ("trust_proof", ["proof point", "proof points", "trust-building", "trust signals"]),
        ("operations_fix", ["root cause", "assign an owner", "operational"]),
        ("messaging_alignment", ["customer language", "site language", "messaging"]),
    ]

    for family, markers in families:
        if any(marker in combined for marker in markers):
            return family

    cleaned = re.sub(r"[^a-z0-9 ]+", " ", combined)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    head = " ".join(cleaned.split()[:8])
    return head


def _is_better(a: Insight, b: Insight) -> bool:
    priority_rank = {"Immediate": 3, "Next": 2, "Monitor": 1}
    severity_rank = {"critical": 3, "warning": 2, "info": 1}

    a_p = priority_rank.get(str(a.get("priority")), 0)
    b_p = priority_rank.get(str(b.get("priority")), 0)
    if a_p != b_p:
        return a_p > b_p

    a_s = severity_rank.get(str(a.get("severity")), 0)
    b_s = severity_rank.get(str(b.get("severity")), 0)
    if a_s != b_s:
        return a_s > b_s

    a_len = len(str(a.get("summary") or ""))
    b_len = len(str(b.get("summary") or ""))
    return a_len > b_len


def _sort_insights(items: List[Insight]) -> List[Insight]:
    priority_rank = {"Immediate": 0, "Next": 1, "Monitor": 2}
    section_rank = {name: idx for idx, name in enumerate(SECTION_ORDER)}

    return sorted(
        items,
        key=lambda x: (
            section_rank.get(str(x.get("section")), 999),
            priority_rank.get(str(x.get("priority")), 999),
            0 if str(x.get("severity") or "").lower() == "warning" else 1,
            str(x.get("summary") or "").lower(),
        ),
    )


def _group_sections(items: List[Insight]) -> List[Dict[str, Any]]:
    grouped: List[Dict[str, Any]] = []

    for section_name in SECTION_ORDER:
        section_items = [i for i in items if i.get("section") == section_name]
        if not section_items:
            continue

        if len(section_items) == 1 and section_items[0].get("priority") == "Monitor":
            continue

        if section_name == SECTION_POSITIONING:
            if all(i.get("type") != "hidden_opportunity" for i in section_items):
                continue

        if section_name == SECTION_MESSAGING:
            has_rollup = any(i.get("type") == "messaging_rollup" for i in section_items)
            if not has_rollup:
                continue

        grouped.append(
            {
                "section": section_name,
                "count": len(section_items),
                "insights": [_strip_internal_fields(i) for i in section_items],
            }
        )

    return grouped


def _strip_internal_fields(item: Insight) -> Insight:
    cleaned = dict(item)
    cleaned.pop("_dedupe_key", None)
    return cleaned


def _build_dedupe_key(item: Insight) -> str:
    insight_type = str(item.get("type") or "").strip().lower()
    section = str(item.get("section") or "").strip().lower()
    summary = str(item.get("summary") or "").strip().lower()
    action = str(item.get("action") or "").strip().lower()
    # -----------------------------------------
    # Strategic positioning dedupe
    # -----------------------------------------

    if (
        "positioning opening" in summary
        or "reposition by" in summary
    ):
        if (
            "speed" in summary
            or "convenience" in summary
        ):
            return "positioning_speed_convenience"

        if (
            "trust" in summary
            or "communication" in summary
        ):
            return "positioning_trust"

        return "positioning_generic"

    normalized_summary = re.sub(r"[^a-z0-9 ]+", " ", summary)
    normalized_summary = re.sub(r"\s+", " ", normalized_summary).strip()

    normalized_action = re.sub(r"[^a-z0-9 ]+", " ", action)
    normalized_action = re.sub(r"\s+", " ", normalized_action).strip()

    summary_head = " ".join(normalized_summary.split()[:8])
    action_head = " ".join(normalized_action.split()[:6])

    if insight_type == "hidden_opportunity":
        owner_themes = _hidden_opportunity_owner_themes(item)
        if owner_themes:
            return f"{section}|{insight_type}|{'-'.join(owner_themes)}"

    return f"{section}|{insight_type}|{summary_head}|{action_head}"


def _display_order(section: str, priority: str) -> int:
    section_rank = {name: idx for idx, name in enumerate(SECTION_ORDER)}
    priority_rank = {"Immediate": 0, "Next": 1, "Monitor": 2}
    return (section_rank.get(section, 99) * 10) + priority_rank.get(priority, 9)


def _clean_sentence(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"\s+", " ", t)

    if not t:
        return ""

    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    t = t[0].upper() + t[1:] if t else t
    return t


def _human_join(values: List[str]) -> str:
    vals = [str(v).strip() for v in values if str(v).strip()]
    if not vals:
        return ""
    if len(vals) == 1:
        return vals[0]
    if len(vals) == 2:
        return f"{vals[0]} and {vals[1]}"
    return f"{', '.join(vals[:-1])}, and {vals[-1]}"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _insight_identity(item: Insight) -> Tuple[str, str]:
    insight_type = str(item.get("type") or "").strip().lower()
    details = item.get("details") or {}

    competitor_name = str(
        details.get("competitor_name")
        or details.get("leader_name")
        or details.get("challenger_name")
        or ""
    ).strip().lower()

    if insight_type == "hidden_opportunity":
        owner_themes = _hidden_opportunity_owner_themes(item)
        if owner_themes:
            return insight_type, "|".join(owner_themes)

    if competitor_name:
        return insight_type, competitor_name

    return insight_type, ""


def _meaningful_movement_for_type(current: Insight, previous: Insight) -> bool:
    current_type = str(current.get("type") or "").strip().lower()
    cur_details = current.get("details") or {}
    prev_details = previous.get("details") or {}

    cur_rank_delta = _safe_int(
        cur_details.get("rank_delta", cur_details.get("owner_rank_delta"))
    )
    prev_rank_delta = _safe_int(
        prev_details.get("rank_delta", prev_details.get("owner_rank_delta"))
    )
    if abs(cur_rank_delta) >= 1 and cur_rank_delta != prev_rank_delta:
        return True

    cur_owner_rank = _safe_int(cur_details.get("owner_rank"), default=999)
    prev_owner_rank = _safe_int(prev_details.get("owner_rank"), default=999)
    if cur_owner_rank != 999 and prev_owner_rank != 999 and cur_owner_rank != prev_owner_rank:
        return True

    cur_share_delta = _safe_float(
        cur_details.get("share_delta_pp", cur_details.get("share_change_pp"))
    )
    prev_share_delta = _safe_float(
        prev_details.get("share_delta_pp", prev_details.get("share_change_pp"))
    )
    if abs(cur_share_delta) >= 1.0 and abs(cur_share_delta - prev_share_delta) >= 0.5:
        return True

    cur_owner_share = _safe_float(
        cur_details.get("owner_share_pct", cur_details.get("share_pct"))
    )
    prev_owner_share = _safe_float(
        prev_details.get("owner_share_pct", prev_details.get("share_pct"))
    )
    if cur_owner_share and prev_owner_share and abs(cur_owner_share - prev_owner_share) >= 1.0:
        return True

    cur_review_delta = _safe_int(
        cur_details.get("review_delta", cur_details.get("review_gain"))
    )
    prev_review_delta = _safe_int(
        prev_details.get("review_delta", prev_details.get("review_gain"))
    )
    if abs(cur_review_delta) >= 5 and cur_review_delta != prev_review_delta:
        return True

    cur_reviews_total = _safe_int(
        cur_details.get("reviews_total", cur_details.get("owner_reviews_total"))
    )
    prev_reviews_total = _safe_int(
        prev_details.get("reviews_total", prev_details.get("owner_reviews_total"))
    )
    if cur_reviews_total and prev_reviews_total and abs(cur_reviews_total - prev_reviews_total) >= 10:
        return True

    cur_comp = str(
        cur_details.get("competitor_name")
        or cur_details.get("leader_name")
        or cur_details.get("challenger_name")
        or ""
    ).strip().lower()
    prev_comp = str(
        prev_details.get("competitor_name")
        or prev_details.get("leader_name")
        or prev_details.get("challenger_name")
        or ""
    ).strip().lower()
    if cur_comp and prev_comp and cur_comp != prev_comp:
        return True

    if current_type == "market_concentration":
        cur_top1 = _safe_float(cur_details.get("top1_share_pct"))
        prev_top1 = _safe_float(prev_details.get("top1_share_pct"))
        cur_top2 = _safe_float(cur_details.get("top2_share_pct"))
        prev_top2 = _safe_float(prev_details.get("top2_share_pct"))
        if abs(cur_top1 - prev_top1) >= 1.0 or abs(cur_top2 - prev_top2) >= 1.0:
            return True

    if current_type == "market_quiet":
        cur_market_total = _safe_int(cur_details.get("market_total_reviews"))
        prev_market_total = _safe_int(prev_details.get("market_total_reviews"))
        if cur_market_total and prev_market_total and abs(cur_market_total - prev_market_total) >= 10:
            return True

    return False


def _should_keep_repeated_insight(current: Insight, previous: Optional[Insight]) -> bool:
    if previous is None:
        return True

    current_type = str(current.get("type") or "").strip().lower()
    if current_type not in CHANGE_AWARE_TYPES:
        return True

    cur_severity = str(current.get("severity") or "").strip().lower()
    prev_severity = str(previous.get("severity") or "").strip().lower()
    if cur_severity != prev_severity:
        return True

    cur_priority = str(current.get("priority") or "").strip()
    prev_priority = str(previous.get("priority") or "").strip()
    if cur_priority != prev_priority:
        return True

    if _meaningful_movement_for_type(current, previous):
        return True

    return False


def _build_previous_lookup(previous_insights: Optional[List[Insight]]) -> Dict[Tuple[str, str], Insight]:
    lookup: Dict[Tuple[str, str], Insight] = {}
    if not previous_insights:
        return lookup

    normalized_previous: List[Insight] = []
    for item in previous_insights:
        if not isinstance(item, dict):
            continue
        try:
            normalized_previous.append(_normalize_insight(item))
        except Exception:
            continue

    for item in normalized_previous:
        identity = _insight_identity(item)
        if identity not in lookup:
            lookup[identity] = item

    return lookup


def _suppress_stale_repeated_insights(
    items: List[Insight],
    previous_insights: Optional[List[Insight]] = None,
) -> List[Insight]:
    if not items or not previous_insights:
        return items

    previous_lookup = _build_previous_lookup(previous_insights)
    filtered: List[Insight] = []

    for item in items:
        identity = _insight_identity(item)
        previous = previous_lookup.get(identity)

        if _should_keep_repeated_insight(item, previous):
            filtered.append(item)

    return filtered

# -------------------------
# Review Target Recommendation
# -------------------------

def build_review_target_recommendation(
    owner_review_count: int | None,
    competitor_rows: list[dict] | None,
) -> dict | None:

    if owner_review_count is None or owner_review_count < 0:
        return None

    rows = competitor_rows or []

    valid_competitors = []
    for row in rows:
        name = row.get("competitor_name") or row.get("name") or "Competitor"
        count = (
            row.get("review_count")
            or row.get("google_review_count")
            or row.get("reviews_total")
        )

        if count is None:
            continue

        try:
            count = int(count)
        except:
            continue

        valid_competitors.append({
            "name": name,
            "review_count": count,
            "is_business": bool(row.get("is_business")),
        })

    if not valid_competitors:
        return None

    if not any(r["is_business"] for r in valid_competitors):
        valid_competitors.append({
            "name": "You",
            "review_count": int(owner_review_count),
            "is_business": True,
        })

    sorted_rows = sorted(valid_competitors, key=lambda r: r["review_count"], reverse=True)

    owner_index = next((i for i, r in enumerate(sorted_rows) if r["is_business"]), None)
    if owner_index is None:
        return None

    current_rank = owner_index + 1
    leader_count = sorted_rows[0]["review_count"]

    gap_to_leader = max(leader_count - owner_review_count, 0)

    gap_to_next = None
    next_competitor = None

    if owner_index > 0:
        next_competitor = sorted_rows[owner_index - 1]
        gap_to_next = max(next_competitor["review_count"] - owner_review_count, 0)

    # -------------------------
    # Logic tiers
    # -------------------------

    if current_rank == 1:
        mode = "Defend mode"

        base = max(6, round(owner_review_count * 0.015))
        low = base
        high = base + 4

        headline = f"This month: maintain {low}–{high} new reviews to preserve and extend your lead."

    elif gap_to_next is not None and gap_to_next <= 15:
        mode = "Growth mode"

        low = max(8, gap_to_next + 3)
        high = low + 5

        headline = f"This month: generate {low}–{high} reviews to overtake the next competitor."

    elif gap_to_leader >= 75:
        mode = "Catch-up mode"

        low = min(max(20, round(gap_to_leader * 0.12)), 35)
        high = min(low + 10, 50)

        headline = f"This month: target {low}–{high} new reviews to begin closing the gap."

    else:
        mode = "Growth mode"

        low = min(max(10, round(gap_to_leader * 0.10)), 25)
        high = low + 5

        headline = f"This month: generate {low}–{high} new reviews to improve your position."

    return {
        "mode": mode,
        "current_rank": current_rank,
        "gap_to_leader": gap_to_leader,
        "gap_to_next": gap_to_next,
        "next_competitor": next_competitor["name"] if next_competitor else None,
        "low": int(low),
        "high": int(high),
        "headline": headline,
    }

def build_review_pace_message(
    review_target: dict | None,
    owner_review_delta_30d: int | None,
) -> dict | None:
    if not review_target:
        return None

    if owner_review_delta_30d is None:
        return None

    try:
        current_pace = int(owner_review_delta_30d)
    except (TypeError, ValueError):
        return None

    target_low = int(review_target.get("low") or 0)
    target_high = int(review_target.get("high") or 0)

    if target_low <= 0 or target_high <= 0:
        return None

    if current_pace >= target_low:
        status = "On pace"
        message = (
            f"Current pace: +{current_pace} reviews/month. "
            f"You are on pace for the recommended {target_low}–{target_high} review target."
        )
    elif current_pace >= max(1, round(target_low * 0.6)):
        status = "Slightly behind pace"
        needed = target_low - current_pace
        message = (
            f"Current pace: +{current_pace} reviews/month. "
            f"You need about {needed} more reviews this month to reach the recommended target."
        )
    else:
        status = "Behind pace"
        needed = target_low - current_pace
        message = (
            f"Current pace: +{current_pace} reviews/month. "
            f"Review generation needs to increase to reach the recommended {target_low}–{target_high} target."
        )

    return {
        "status": status,
        "current_pace": current_pace,
        "target_low": target_low,
        "target_high": target_high,
        "message": message,
    }

def build_review_overtake_projection(
    review_target: dict | None,
    owner_review_delta_30d: int | None,
    next_competitor_delta_30d: int | None,
) -> dict | None:
    if not review_target:
        return None

    gap_to_next = review_target.get("gap_to_next")
    next_competitor = review_target.get("next_competitor")

    if gap_to_next is None or not next_competitor:
        return None

    try:
        gap = int(gap_to_next)
        owner_pace = int(owner_review_delta_30d or 0)
        competitor_pace = int(next_competitor_delta_30d or 0)
    except (TypeError, ValueError):
        return None

    net_gain_per_month = owner_pace - competitor_pace

    if gap <= 0:
        return {
            "status": "Already ahead",
            "message": f"You are already ahead of {next_competitor}.",
            "months_to_overtake": 0,
        }

    if net_gain_per_month <= 0:
        # Not closing — show what it would take to close in 6 or 12 months
        aggressive_months = 6
        moderate_months = 12
        aggressive_target = max(1, round(gap / aggressive_months))
        moderate_target = max(1, round(gap / moderate_months))

        return {
            "status": "Not closing yet",
            "message": (
                f"At the current pace, you are not closing the gap with {next_competitor}. "
                f"To close within a year, you need to outpace them by roughly "
                f"{moderate_target}–{aggressive_target} reviews per month."
            ),
            "months_to_overtake": None,
            "gap": gap,
            "monthly_target_to_close_12mo": moderate_target,
            "monthly_target_to_close_6mo": aggressive_target,
        }

    months = max(1, round(gap / net_gain_per_month))

    return {
        "status": "Closing gap",
        "message": (
            f"At the current pace, you could overtake {next_competitor} in about "
            f"{months} month{'s' if months != 1 else ''}."
        ),
        "months_to_overtake": months,
        "net_gain_per_month": net_gain_per_month,
    }

def _build_execution_detail(title: str) -> str:
    t = (title or "").lower()

    if "speed" in t or "quality" in t:
        return (
            "This month: update 1 homepage headline, add 1 proof point (review or stat), "
            "and update 1 follow-up message to reinforce this positioning."
        )

    if "leads by" in t or "ahead" in t or "behind" in t:
        return (
            "This month: generate 12–20 new reviews and monitor ranking movement vs competitors."
        )

    return (
        "This month: take one visible action that reinforces this insight in customer-facing messaging."
    )