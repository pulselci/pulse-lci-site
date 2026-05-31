from __future__ import annotations

from typing import Any, Dict, List, Optional


def _strategy_fields(
    insight_type: str,
    *,
    severity: str = "info",
) -> Dict[str, str]:
    """
    Deterministic strategy wrapper for client-facing perceived value.
    Safe helper: only adds extra display fields, does not change existing logic.
    """
    priority = "medium"
    implication = "This is worth monitoring."
    recommended_action = "Monitor this trend and adjust positioning if it continues."

    if insight_type == "competitor_surge":
        priority = "high"
        implication = "A competitor is gaining attention faster than the rest of the market."
        recommended_action = (
            "Increase review requests immediately and reinforce your strongest "
            "differentiators in customer-facing messaging."
        )

    elif insight_type == "competitive_tier_pressure":
        priority = "high"
        implication = "Your current market position is vulnerable from one side while still leaving upside above you."
        recommended_action = (
            "Protect your current rank with a short-term review push while targeting "
            "the competitor directly above you."
        )

    elif insight_type == "challenger_gap":
        priority = "medium" if severity == "info" else "high"
        implication = "The current market leader has built measurable review-distance from the field."
        recommended_action = (
            "Set a near-term review growth target and build messaging that gives "
            "customers a reason to choose you over the leader."
        )

    elif insight_type == "leader_pulling_away":
        priority = "high"
        implication = "The market leader is not just ahead — they are extending the gap."
        recommended_action = (
            "Respond quickly with a focused review-generation campaign before the "
            "gap becomes harder to close."
        )

    elif insight_type == "market_quiet":
        priority = "medium"
        implication = "The market is temporarily flat, which creates an opening for a business that acts first."
        recommended_action = "Use this quiet period to generate new reviews and create separation."

    elif insight_type == "market_concentration":
        priority = "medium" if severity == "info" else "high"
        implication = "A small number of competitors control most of the market’s trust signals."
        recommended_action = (
            "Win customers from the market leader by highlighting clear differentiators, "
            "stronger trust signals, and clear reasons customers should choose you instead."
        )

    elif insight_type == "baseline_rank":
        priority = "medium"
        implication = "Your current rank sets the starting point for future movement."
        recommended_action = "Use this baseline to decide whether you should defend your position or push for the next spot."

    elif insight_type == "leader_gap":
        priority = "medium"
        implication = "The review gap to the leader shows how much ground you need to make up."
        recommended_action = "Set a realistic review growth target and track whether the gap is shrinking month over month."

    elif insight_type == "market_dominance":
        priority = "high" if severity == "warning" else "medium"
        implication = "One competitor currently holds outsized review share in the market."
        recommended_action = "Win customers from the market leader by clearly positioning around your strongest advantage."

    elif insight_type == "market_position":
        priority = "medium"
        implication = "Your market tier affects whether the right move is defense, offense, or separation."
        recommended_action = "Tailor your review strategy to your tier: defend if vulnerable, climb if close, or widen the lead if ahead."

    return {
        "priority": priority,
        "implication": implication,
        "recommended_action": recommended_action,
    }


def _extract_owner_share_pct(share_of_voice: Optional[Dict[str, Any]]) -> Optional[float]:
    if not isinstance(share_of_voice, dict):
        return None

    rows = share_of_voice.get("rows") or []
    if not isinstance(rows, list):
        return None

    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("is_business"):
            try:
                value = row.get("share_pct")
                if value is None:
                    return None
                return float(value)
            except Exception:
                return None

    return None


def _build_share_change_text(
    current_share_pct: Optional[float],
    previous_share_pct: Optional[float],
) -> str:
    if current_share_pct is None or previous_share_pct is None:
        return ""

    try:
        delta = round(float(current_share_pct) - float(previous_share_pct), 1)
    except Exception:
        return ""

    if abs(delta) < 0.1:
        return " (no meaningful change vs last report)"

    if delta > 0:
        return f" (up {abs(delta):.1f}pp vs last report)"

    return f" (down {abs(delta):.1f}pp vs last report)"


def add_competitor_surge_insight(
    sections: Dict[str, Any],
    *,
    min_review_delta: int = 3,
    min_ratio: float = 2.0,
    max_items: int = 2,
) -> None:
    """
    Competitor Surge (deterministic):
      - Reads rows from sections["momentum"] (we pass competitor_deltas from routes).
      - Uses reviews_delta_7d (fallback reviews_delta_1d) as the "review delta".
      - Computes market_avg_delta = sum(positive deltas) / competitor_count
      - Flags competitors with delta >= min_review_delta AND delta/market_avg >= min_ratio
      - Appends into sections["insights"] as:
          {"type": "competitor_surge", "items": [ ... ]}
      - Omits itself if no items qualify.
    """
    rows = sections.get("momentum") or []
    if not isinstance(rows, list) or not rows:
        return

    def _get_delta(r: Dict[str, Any]) -> Optional[int]:
        for k in ("review_delta", "reviews_delta", "reviews_delta_7d", "reviews_delta_1d"):
            if k in r and r.get(k) is not None:
                try:
                    return int(r.get(k))
                except Exception:
                    return None
        return None

    deltas: List[tuple[str, int]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = (r.get("competitor_name") or r.get("name") or r.get("competitor") or "").strip()
        if not name:
            continue
        d = _get_delta(r)
        if d is None:
            continue
        deltas.append((name, d))

    if not deltas:
        return

    competitor_count = len(deltas)
    market_total_delta = sum(max(d, 0) for _, d in deltas)
    if competitor_count <= 0 or market_total_delta <= 0:
        return

    market_avg = market_total_delta / competitor_count

    hits: List[tuple[float, str, int]] = []
    for name, d in deltas:
        if d < min_review_delta:
            continue
        ratio = d / market_avg if market_avg > 0 else 0.0
        if ratio >= min_ratio:
            hits.append((ratio, name, d))

    if not hits:
        return

    hits.sort(reverse=True)

    out: List[Dict[str, Any]] = []
    for ratio, name, d in hits[:max_items]:
        out.append(
            {
                "competitor": name,
                "review_delta": d,
                "market_avg_delta": round(market_avg, 1),
                "surge_ratio": round(ratio, 1),
                "message": (
                    f"YOU vs THEM: {name} is gaining reviews {round(ratio, 1)}× faster than the market this period. "
                    "If you don't respond, they’ll widen the gap."
                ),
            }
        )

    insights = sections.get("insights")
    if not isinstance(insights, list):
        insights = []
        sections["insights"] = insights

    insights.append({"type": "competitor_surge", "items": out})


def add_competitive_tier_pressure_insight(sections: Dict[str, Any]) -> None:
    sov = (sections or {}).get("share_of_voice") or {}
    rows = sov.get("rows") or []
    if not isinstance(rows, list) or len(rows) < 3:
        return

    insights = sections.get("insights")
    if not isinstance(insights, list):
        insights = []
        sections["insights"] = insights

    if any(isinstance(x, dict) and x.get("type") == "competitive_tier_pressure" for x in insights):
        return

    business = None
    above = None
    below = None

    for i, r in enumerate(rows):
        if r.get("is_business"):
            business = r
            if i > 0:
                above = rows[i - 1]
            if i < len(rows) - 1:
                below = rows[i + 1]
            break

    if not business or not above or not below:
        return

    gap_to_above = int(above.get("reviews_total") or 0) - int(business.get("reviews_total") or 0)
    gap_to_below = int(business.get("reviews_total") or 0) - int(below.get("reviews_total") or 0)

    pressure_from = "below" if gap_to_below < gap_to_above else "above"

    severity = "info"
    strategy = _strategy_fields("competitive_tier_pressure", severity=severity)

    insights.append(
        {
            "type": "competitive_tier_pressure",
            "severity": severity,
            "priority": strategy["priority"],
            "implication": strategy["implication"],
            "recommended_action": strategy["recommended_action"],
            "summary": (
                f"YOU vs THEM: {above.get('competitor_name')} is ahead by {gap_to_above} reviews. "
                f"You're only {gap_to_below} reviews ahead of {below.get('competitor_name')}. "
                f"Pressure is coming from {pressure_from} - protect your spot and chip away at the gap."
            ),
            "details": {
                "business_reviews": int(business.get("reviews_total") or 0),
                "above": {
                    "name": above.get("competitor_name"),
                    "reviews": int(above.get("reviews_total") or 0),
                    "gap": gap_to_above,
                },
                "below": {
                    "name": below.get("competitor_name"),
                    "reviews": int(below.get("reviews_total") or 0),
                    "gap": gap_to_below,
                },
                "pressure_from": pressure_from,
            },
        }
    )


def add_challenger_gap_insight(
    sections: Dict[str, Any],
    *,
    min_gap_reviews: int = 25,
) -> None:
    """
    Challenger Gap (deterministic):
      - Uses share_of_voice rows (ranked) to compare #1 vs #2.
      - Fires when leader_reviews - challenger_reviews >= min_gap_reviews.
      - Severity: warning at >= 100 reviews gap, else info.
      - Appends into sections["insights"] as a single object.
      - Omits itself if insufficient rows, tie, or small gap.
      - Owner-aware wording:
          * if owner is #1 -> defend the lead
          * if owner is #2 -> close the gap
          * otherwise -> neutral market summary
    """
    sov = (sections or {}).get("share_of_voice") or {}
    rows = sov.get("rows") or []
    if not isinstance(rows, list) or len(rows) < 2:
        return

    ranked = [r for r in rows if isinstance(r, dict)]
    if len(ranked) < 2:
        return

    leader, challenger = ranked[0], ranked[1]

    gap = int(leader.get("reviews_total") or 0) - int(challenger.get("reviews_total") or 0)
    if gap <= 0 or gap < int(min_gap_reviews):
        return

    severity = "warning" if gap >= 100 else "info"

    insights = sections.get("insights")
    if not isinstance(insights, list):
        insights = []
        sections["insights"] = insights

    if any(isinstance(x, dict) and x.get("type") == "challenger_gap" for x in insights):
        return

    strategy = _strategy_fields("challenger_gap", severity=severity)

    owner = next((r for r in ranked if r.get("is_business")), None)

    owner_name = str(owner.get("competitor_name") or "You").strip() if owner else "You"
    leader_name = str(leader.get("competitor_name") or "Market leader").strip()
    challenger_name = str(challenger.get("competitor_name") or "Challenger").strip()

    owner_is_leader = bool(owner) and (
        leader_name.strip().lower() == owner_name.strip().lower()
    )
    owner_is_challenger = bool(owner) and (
        challenger_name.strip().lower() == owner_name.strip().lower()
    )

    if owner_is_leader:
        summary = (
            f"YOU vs THEM: you are leading the market by {gap} reviews over "
            f"{challenger_name}. The priority is defending that lead and widening separation."
        )
    elif owner_is_challenger:
        summary = (
            f"YOU vs THEM: you trail {leader_name} by {gap} reviews. "
            f"If you want the #1 spot, you need to close that gap."
        )
    else:
        summary = (
            f"YOU vs THEM: {leader_name} is leading the market by {gap} reviews over "
            f"{challenger_name}."
        )

    insights.append(
        {
            "type": "challenger_gap",
            "severity": severity,
            "priority": strategy["priority"],
            "implication": strategy["implication"],
            "recommended_action": strategy["recommended_action"],
            "summary": summary,
            "details": {
                "leader_name": leader_name,
                "challenger_name": challenger_name,
                "gap_reviews": gap,
                "leader_reviews_total": int(leader.get("reviews_total") or 0),
                "challenger_reviews_total": int(challenger.get("reviews_total") or 0),
                "leader_share_pct": float(leader.get("share_pct") or 0.0),
                "challenger_share_pct": float(challenger.get("share_pct") or 0.0),
                "owner_name": owner_name,
                "owner_is_leader": owner_is_leader,
                "owner_is_challenger": owner_is_challenger,
                "is_owner": owner_is_leader or owner_is_challenger,
                "thresholds": {
                    "min_gap_reviews": int(min_gap_reviews),
                    "warning_gap_reviews": 100,
                },
            },
        }
    )


def add_leader_pulling_away_insight(
    sections: Dict[str, Any],
    *,
    min_gap_widening: int = 5,
    warning_gap_widening: int = 15,
) -> None:
    sov = (sections or {}).get("share_of_voice") or {}
    rows = sov.get("rows") or []
    if not isinstance(rows, list) or len(rows) < 2:
        return

    leader_name = (rows[0] or {}).get("competitor_name")
    challenger_name = (rows[1] or {}).get("competitor_name")
    if not leader_name or not challenger_name:
        return

    items = sections.get("momentum") or []
    if not isinstance(items, list) or not items:
        return

    def _delta_for(name: str) -> Optional[int]:
        for it in items:
            if not isinstance(it, dict):
                continue
            n = (it.get("competitor_name") or it.get("name") or it.get("competitor") or "").strip()
            if n != name:
                continue
            for k in ("reviews_delta_7d", "review_delta", "reviews_delta", "reviews_delta_1d"):
                v = it.get(k)
                if v is not None:
                    try:
                        return int(v)
                    except Exception:
                        return None
        return None

    leader_d7 = _delta_for(leader_name)
    challenger_d7 = _delta_for(challenger_name)
    if leader_d7 is None or challenger_d7 is None:
        return

    gap_change_7d = leader_d7 - challenger_d7
    if gap_change_7d < int(min_gap_widening):
        return

    insights = sections.get("insights")
    if not isinstance(insights, list):
        insights = []
        sections["insights"] = insights

    if any(isinstance(x, dict) and x.get("type") == "leader_pulling_away" for x in insights):
        return

    severity = "warning" if gap_change_7d >= int(warning_gap_widening) else "info"
    strategy = _strategy_fields("leader_pulling_away", severity=severity)

    insights.append(
        {
            "type": "leader_pulling_away",
            "severity": severity,
            "priority": strategy["priority"],
            "implication": strategy["implication"],
            "recommended_action": strategy["recommended_action"],
            "summary": (
                f"YOU vs THEM: {leader_name} widened the gap vs {challenger_name} by {gap_change_7d} reviews this week. "
                "They’re pulling away - you’ll need extra review volume to keep pace."
            ),
            "details": {
                "leader_name": leader_name,
                "challenger_name": challenger_name,
                "leader_reviews_delta_7d": int(leader_d7),
                "challenger_reviews_delta_7d": int(challenger_d7),
                "gap_change_7d": int(gap_change_7d),
                "thresholds": {
                    "min_gap_widening": int(min_gap_widening),
                    "warning_gap_widening": int(warning_gap_widening),
                },
            },
        }
    )


def build_baseline_insights(
    share_of_voice: dict,
    previous_share_of_voice: Optional[Dict[str, Any]] = None,
) -> list[dict]:
    """
    Deterministic baseline insights for first-report usefulness.
    These do not depend on deltas and can fire from a single snapshot.

    Optional previous_share_of_voice enables change-aware wording for
    market share / dominance summaries without breaking existing callers.
    """
    rows = share_of_voice.get("rows", [])
    if not rows or not isinstance(rows, list):
        return []

    ranked_rows = sorted(
        [r for r in rows if isinstance(r, dict)],
        key=lambda x: x.get("rank", 999),
    )
    if not ranked_rows:
        return []

    insights: list[dict] = []

    leader = ranked_rows[0]
    owner = next((r for r in ranked_rows if r.get("is_business")), None)
    if not owner:
        return []

    owner_name = str(owner.get("competitor_name") or "You").strip()
    leader_name = str(leader.get("competitor_name") or "the market leader").strip()

    owner_rank = int(owner.get("rank") or 999)
    owner_reviews = int(owner.get("reviews_total") or 0)
    leader_reviews = int(leader.get("reviews_total") or 0)
    owner_is_leader = bool(owner.get("is_business")) and (
        str(owner.get("competitor_name") or "").strip().lower()
        == str(leader.get("competitor_name") or "").strip().lower()
    )

    current_owner_share_pct = _extract_owner_share_pct(share_of_voice)
    previous_owner_share_pct = _extract_owner_share_pct(previous_share_of_voice)
    owner_share_change_text = _build_share_change_text(
        current_owner_share_pct,
        previous_owner_share_pct,
    )

    rank_strategy = _strategy_fields("baseline_rank", severity="info")
    insights.append(
        {
            "type": "baseline_rank",
            "severity": "info",
            "priority": rank_strategy["priority"],
            "implication": rank_strategy["implication"],
            "recommended_action": rank_strategy["recommended_action"],
            "summary": f"You are currently ranked #{owner_rank} in your market based on total Google reviews.",
            "details": {
                "owner_rank": owner_rank,
                "owner_reviews_total": owner_reviews,
                "market_size": len(ranked_rows),
                "is_owner": True,
                "owner_name": owner_name,
            },
        }
    )

    gap = leader_reviews - owner_reviews
    if gap > 0:
        gap_strategy = _strategy_fields("leader_gap", severity="info")
        insights.append(
            {
                "type": "leader_gap",
                "severity": "info",
                "priority": gap_strategy["priority"],
                "implication": gap_strategy["implication"],
                "recommended_action": gap_strategy["recommended_action"],
                "summary": f"You trail the market leader by {gap} reviews.",
                "details": {
                    "leader_name": leader_name,
                    "leader_reviews_total": leader_reviews,
                    "owner_reviews_total": owner_reviews,
                    "gap_reviews": gap,
                    "is_owner": True,
                    "owner_name": owner_name,
                },
            }
        )

    leader_share_pct = float(leader.get("share_pct") or 0.0)
    if leader_share_pct >= 40:
        dominance_strategy = _strategy_fields("market_dominance", severity="warning")

        if owner_is_leader:
            dominance_summary = (
                f"You currently lead the market with {round(leader_share_pct, 1)}% "
                f"of all tracked market reviews{owner_share_change_text}."
            )
        else:
            dominance_summary = (
                f"{leader_name} controls {round(leader_share_pct, 1)}% "
                "of all tracked market reviews."
            )

        insights.append(
            {
                "type": "market_dominance",
                "severity": "warning",
                "priority": dominance_strategy["priority"],
                "implication": dominance_strategy["implication"],
                "recommended_action": dominance_strategy["recommended_action"],
                "summary": dominance_summary,
                "details": {
                    "leader_name": leader_name,
                    "leader_share_pct": round(leader_share_pct, 1),
                    "leader_reviews_total": leader_reviews,
                    "owner_is_leader": owner_is_leader,
                    "is_owner": owner_is_leader,
                    "owner_name": owner_name,
                    "owner_share_pct": round(current_owner_share_pct, 1) if current_owner_share_pct is not None else None,
                    "previous_owner_share_pct": round(previous_owner_share_pct, 1) if previous_owner_share_pct is not None else None,
                    "owner_share_change_text": owner_share_change_text,
                },
            }
        )

    if owner_rank == 1:
        tier_summary = "You currently hold the leading position in your market."
        tier = "leader"
    elif owner_rank <= 3:
        tier_summary = "You are currently in the upper tier of your market."
        tier = "upper tier"
    else:
        tier_summary = "You are currently in the lower tier of your market."
        tier = "lower tier"

    tier_strategy = _strategy_fields("market_position", severity="info")
    insights.append(
        {
            "type": "market_position",
            "severity": "info",
            "priority": tier_strategy["priority"],
            "implication": tier_strategy["implication"],
            "recommended_action": tier_strategy["recommended_action"],
            "summary": tier_summary,
            "details": {
                "owner_rank": owner_rank,
                "tier": tier,
                "market_size": len(ranked_rows),
                "is_owner": True,
                "owner_name": owner_name,
            },
        }
    )

    return insights


def add_market_quiet_insight(
    sections: Dict[str, Any],
    window_days: int = 7,
) -> None:
    """
    Adds an insight when the market is flat: no competitor gained reviews in the window.
    Deterministic and explainable.
    Input: sections["momentum"] list (we're using it as a generic deltas list in routes.py)
    Requires each item to have reviews_delta_7d (int-ish) and competitor_name.
    Output: appends a single {type:"market_quiet", ...} into sections["insights"].
    """
    items = sections.get("momentum") or []
    if not isinstance(items, list) or not items:
        return

    insights = sections.get("insights") or []
    if not isinstance(insights, list):
        insights = []

    if any(isinstance(x, dict) and x.get("type") == "market_quiet" for x in insights):
        sections["insights"] = insights
        return

    if any(isinstance(x, dict) and x.get("type") == "competitor_surge" for x in insights):
        sections["insights"] = insights
        return

    if any(
        isinstance(x, dict)
        and x.get("type") in ("competitive_tier_pressure", "challenger_gap", "leader_pulling_away")
        for x in insights
    ):
        sections["insights"] = insights
        return

    deltas: List[int] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            deltas.append(int(it.get("reviews_delta_7d") or 0))
        except Exception:
            deltas.append(0)

    if not deltas:
        sections["insights"] = insights
        return

    if max(deltas) == 0:
        insights.append(
            {
                "type": "market_quiet",
                "window_days": int(window_days),
                "competitor_count": int(len(items)),
                "message": (
                    f"YOU vs THEM: No one gained new reviews in the last {int(window_days)} days. "
                    "This is your chance to create separation - push review requests this week."
                ),
                "inputs": {"max_reviews_delta_window": 0},
            }
        )

    sections["insights"] = insights


def suppress_market_quiet_if_owner_centric(sections: Dict[str, Any]) -> None:
    """
    Step 1: If any owner-centric insight exists, remove market_quiet.
    This is order-independent (safe even if market_quiet is added earlier).
    """
    insights = (sections or {}).get("insights")
    if not isinstance(insights, list) or not insights:
        return

    owner_centric = {"competitive_tier_pressure", "challenger_gap", "leader_pulling_away"}
    types_now = {str(i.get("type", "")) for i in insights if isinstance(i, dict)}
    if not (types_now & owner_centric):
        return

    sections["insights"] = [
        i for i in insights
        if not (isinstance(i, dict) and i.get("type") == "market_quiet")
    ]


def add_market_concentration_insight(sections: Dict[str, Any]) -> None:
    sov = (sections or {}).get("share_of_voice") or {}
    rows = sov.get("rows") or []
    total = sov.get("market_total_reviews")

    if total is None:
        total = sum(
            int((r or {}).get("reviews_total") or (r or {}).get("google_review_count") or 0)
            for r in rows
        )

    if not rows or not total or total <= 0:
        return

    if any(isinstance(x, dict) and x.get("type") == "market_concentration" for x in sections.get("insights", [])):
        return

    ranked = sorted(
        [
            {
                "name": (r.get("competitor_name") or r.get("name") or r.get("label") or "Unknown"),
                "reviews": int((r or {}).get("reviews_total") or (r or {}).get("google_review_count") or 0),
            }
            for r in rows
        ],
        key=lambda x: x["reviews"],
        reverse=True,
    )

    if len(ranked) < 2:
        return

    t1, t2 = ranked[0], ranked[1]
    top1_share = t1["reviews"] / total
    top2_share = (t1["reviews"] + t2["reviews"]) / total

    if top1_share < 0.50 and top2_share < 0.70:
        return

    sections.setdefault("insights", []).append(
        {
            "type": "market_concentration",
            "severity": "warning" if top2_share >= 0.80 else "info",
            "summary": (
                f"YOU vs THEM: The top 2 competitors control {round(top2_share * 100)}% of all reviews. "
                "To win share, focus on stealing attention from the top players."
            ),
            "details": {
                "market_total_reviews": total,
                "top1": {"name": t1["name"], "reviews": t1["reviews"], "share": round(top1_share, 3)},
                "top2": {"name": t2["name"], "reviews": t2["reviews"], "share": round(t2["reviews"] / total, 3)},
                "top1_share": round(top1_share, 3),
                "top2_share": round(top2_share, 3),
                "thresholds": {"top1": 0.50, "top2": 0.70},
            },
        }
    )


def add_weekly_actions_insight(sections: Dict[str, Any]) -> None:
    """
    Step 3: Deterministic "What to do this week" recommendations.
    Uses existing sections only (share_of_voice + top_moves/momentum deltas).
    Output:
      {"type":"weekly_actions","items":[...]}
    """
    insights = sections.get("insights")
    if not isinstance(insights, list):
        insights = []
        sections["insights"] = insights

    if any(isinstance(x, dict) and x.get("type") == "weekly_actions" for x in insights):
        return

    sov = (sections or {}).get("share_of_voice") or {}
    rows = sov.get("rows") or []
    if not isinstance(rows, list) or not rows:
        return

    business = None
    above = None
    below = None
    for i, r in enumerate(rows):
        if isinstance(r, dict) and r.get("is_business"):
            business = r
            if i > 0:
                above = rows[i - 1]
            if i < len(rows) - 1:
                below = rows[i + 1]
            break

    if not business:
        return

    biz_reviews = int(business.get("reviews_total") or 0)

    deltas_list = sections.get("top_moves") or sections.get("momentum") or []
    d7_map: Dict[str, int] = {}
    if isinstance(deltas_list, list):
        for it in deltas_list:
            if not isinstance(it, dict):
                continue
            name = (it.get("competitor_name") or it.get("name") or it.get("competitor") or "").strip()
            if not name:
                continue
            v = it.get("reviews_delta_7d")
            if v is None:
                v = it.get("review_delta") or it.get("reviews_delta") or it.get("reviews_delta_1d") or 0
            try:
                d7_map[name] = int(v)
            except Exception:
                d7_map[name] = 0

    items: List[Dict[str, Any]] = []

    if any(isinstance(x, dict) and x.get("type") == "market_quiet" for x in insights):
        items.append(
            {
                "title": "Push review requests (market is quiet)",
                "why": "No competitors gained reviews recently. A small push can create separation fast.",
                "metric": "Target: +5 reviews this week",
                "severity": "high",
            }
        )

    if isinstance(above, dict):
        above_name = above.get("competitor_name") or "the competitor above you"
        above_reviews = int(above.get("reviews_total") or 0)
        gap_to_above = above_reviews - biz_reviews
        if gap_to_above > 0:
            target = min(25, max(5, int(round(gap_to_above * 0.10))))
            items.append(
                {
                    "title": f"Close the gap vs {above_name}",
                    "why": f"You're behind by {gap_to_above} reviews. Weekly gains compound into rank movement.",
                    "metric": f"Target: +{target} reviews this week",
                    "severity": "medium",
                }
            )

    if isinstance(below, dict):
        below_name = below.get("competitor_name") or "the competitor below you"
        below_reviews = int(below.get("reviews_total") or 0)
        gap_to_below = biz_reviews - below_reviews
        if 0 <= gap_to_below <= 50:
            items.append(
                {
                    "title": f"Defend your spot vs {below_name}",
                    "why": f"You're only {gap_to_below} reviews ahead. A small competitor push could flip ranks.",
                    "metric": "Target: maintain +3 to +5 reviews/week",
                    "severity": "high" if gap_to_below <= 15 else "medium",
                }
            )

    if any(isinstance(x, dict) and x.get("type") == "competitor_surge" for x in insights):
        items.append(
            {
                "title": "Respond to competitor surge",
                "why": "A competitor is outpacing the market in new reviews. You need extra review volume to keep pace.",
                "metric": "Target: +5 reviews above your normal weekly pace",
                "severity": "high",
            }
        )
    else:
        max_name = None
        max_d7 = 0
        for n, d in d7_map.items():
            if d > max_d7:
                max_d7 = d
                max_name = n
        if max_name and max_d7 >= 3:
            items.append(
                {
                    "title": f"Watch {max_name}'s review pace",
                    "why": f"{max_name} added {max_d7} reviews in the last 7 days. If you stay flat, they widen the gap.",
                    "metric": "Target: match or beat their weekly review pace",
                    "severity": "medium",
                }
            )

    items.append(
        {
            "title": "Ask every happy customer for a review (simple system)",
            "why": "Reviews are the biggest lever in this report. Consistency beats one-off pushes.",
            "metric": "Process: 10 asks/day via text link",
            "severity": "low",
        }
    )

    items = items[:4]

    insights.append({"type": "weekly_actions", "items": items})

def build_executive_headline(sections: Dict[str, Any]) -> str:
    """
    Builds a high-impact, decision-oriented executive headline.
    Deterministic and safe (read-only on sections).
    """

    sov = (sections or {}).get("share_of_voice") or {}
    rows = sov.get("rows") or []

    if not rows:
        return "Positioning opportunity exists, but data is limited this cycle."

    leader = rows[0]
    owner = next((r for r in rows if r.get("is_business")), None)

    if not owner:
        return "Market leader is defined, but your position is unclear."

    leader_name = leader.get("competitor_name") or "the leader"
    owner_rank = int(owner.get("rank") or 0)
    owner_reviews = int(owner.get("reviews_total") or 0)
    leader_reviews = int(leader.get("reviews_total") or 0)

    gap = leader_reviews - owner_reviews

    # Core deterministic framing
    if owner_rank == 1:
        return (
            "You lead the market, but must defend and widen your position before competitors close the gap."
        )

    if gap > 300:
        return (
            f"You are behind {leader_name} by {gap} reviews. Closing this gap requires stronger positioning and consistent review growth."
        )

    if gap > 0:
        return (
            f"You are within striking distance of {leader_name}, but your advantage is not clearly positioned to win decisions."
        )

    return "You have positioning advantages, but they are not yet being fully leveraged to drive growth."