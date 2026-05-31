# app/core/insights/market_movers.py

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def _to_int(x: Any, default: int = 0) -> int:
    try:
        if x is None:
            return default
        return int(x)
    except Exception:
        return default


def _to_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def _norm_name(s: Any) -> str:
    s = (s or "")
    try:
        s = str(s)
    except Exception:
        return ""
    s = s.strip().lower()
    if s.endswith(" self"):
        s = s[:-5].strip()
    return s


def _get_rows(sections: Dict[str, Any]) -> List[Dict[str, Any]]:
    sov = (sections or {}).get("share_of_voice") or {}
    rows = sov.get("rows") if isinstance(sov, dict) else None
    if not isinstance(rows, list):
        return []
    out: List[Dict[str, Any]] = []
    for r in rows:
        if isinstance(r, dict):
            out.append(r)
    return out


def _index_rows(rows: List[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """
    Returns:
      - by_id: competitor_id -> row
      - by_name: normalized competitor_name -> row (fallback)
    """
    by_id: Dict[str, Dict[str, Any]] = {}
    by_name: Dict[str, Dict[str, Any]] = {}

    for r in rows:
        cid = r.get("competitor_id")
        if cid is not None and str(cid).strip() != "":
            by_id[str(cid)] = r

        n = _norm_name(r.get("competitor_name"))
        if n:
            # Keep first seen if duplicates; stable enough for fallback
            by_name.setdefault(n, r)

    return by_id, by_name


def build_market_movers_insight(
    prev_sections: Dict[str, Any],
    latest_sections: Dict[str, Any],
    *,
    min_share_delta_pp: float = 1.0,
    min_review_delta: int = 10,
) -> Optional[Dict[str, Any]]:
    """
    Step 6: Market-wide movers between previous and latest report.

    Uses competitor_id to match rows first; falls back to normalized name.

    Emits:
      - rank gainers/losers (any rank movement)
      - share gainers/losers (>= min_share_delta_pp)
      - review gainers (>= min_review_delta)
      - who passed you / you passed (relative to owner row)
    """

    prev_rows = _get_rows(prev_sections or {})
    latest_rows = _get_rows(latest_sections or {})
    if not prev_rows or not latest_rows:
        return None

    prev_by_id, prev_by_name = _index_rows(prev_rows)
    latest_by_id, latest_by_name = _index_rows(latest_rows)

    # Owner detection (from latest)
    owner_latest = next((r for r in latest_rows if r.get("is_business") is True), None)
    owner_prev = next((r for r in prev_rows if r.get("is_business") is True), None)

    owner_latest_rank = _to_int(owner_latest.get("rank")) if owner_latest else None
    owner_prev_rank = _to_int(owner_prev.get("rank")) if owner_prev else None

    # Helper to find matching prev row for a latest row
    def _match_prev(latest_row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        cid = latest_row.get("competitor_id")
        if cid is not None and str(cid).strip() != "" and str(cid) in prev_by_id:
            return prev_by_id[str(cid)]
        n = _norm_name(latest_row.get("competitor_name"))
        if n and n in prev_by_name:
            return prev_by_name[n]
        return None

    rank_gainers: List[Dict[str, Any]] = []
    rank_losers: List[Dict[str, Any]] = []
    share_gainers: List[Dict[str, Any]] = []
    share_losers: List[Dict[str, Any]] = []
    review_gainers: List[Dict[str, Any]] = []

    # Passed-you / you-passed tracking
    passed_you: List[Dict[str, Any]] = []
    you_passed: List[Dict[str, Any]] = []

    for lr in latest_rows:
        pr = _match_prev(lr)
        if not pr:
            continue

        old_rank = _to_int(pr.get("rank"))
        new_rank = _to_int(lr.get("rank"))
        rank_delta = new_rank - old_rank  # positive worse, negative better

        old_share = _to_float(pr.get("share_pct"))
        new_share = _to_float(lr.get("share_pct"))
        share_delta = new_share - old_share

        old_reviews = _to_int(pr.get("reviews_total"))
        new_reviews = _to_int(lr.get("reviews_total"))
        review_delta = new_reviews - old_reviews

        name = lr.get("competitor_name") or pr.get("competitor_name") or "Unknown"
        cid = lr.get("competitor_id") or pr.get("competitor_id")
        cid_str = str(cid) if cid is not None and str(cid).strip() != "" else None

        if rank_delta != 0:
            item = {
                "competitor_id": cid_str,
                "competitor_name": name,
                "old_rank": old_rank,
                "new_rank": new_rank,
                "rank_delta": rank_delta,
            }
            if rank_delta < 0:
                rank_gainers.append(item)
            else:
                rank_losers.append(item)

        if abs(share_delta) >= float(min_share_delta_pp):
            item = {
                "competitor_id": cid_str,
                "competitor_name": name,
                "share_delta_pct": round(share_delta, 1),
            }
            if share_delta > 0:
                share_gainers.append(item)
            else:
                share_losers.append(item)

        if review_delta >= int(min_review_delta):
            review_gainers.append(
                {
                    "competitor_id": cid_str,
                    "competitor_name": name,
                    "review_delta": review_delta,
                }
            )

        # passed-you / you-passed: only meaningful if we have owner ranks on both snapshots
        if owner_latest_rank is not None and owner_prev_rank is not None and owner_latest and owner_prev:
            # competitor ranks vs owner ranks
            old_rel = old_rank - owner_prev_rank
            new_rel = new_rank - owner_latest_rank

            # old_rel < 0 means competitor was ahead of owner; >0 means behind owner
            # If old_rel < 0 and new_rel > 0, owner passed them (you_passed)
            # If old_rel > 0 and new_rel < 0, competitor passed owner (passed_you)
            if old_rel < 0 and new_rel > 0:
                you_passed.append(
                    {
                        "competitor_id": cid_str,
                        "competitor_name": name,
                        "old_rank": old_rank,
                        "new_rank": new_rank,
                        "rank_delta": rank_delta,
                    }
                )
            elif old_rel > 0 and new_rel < 0:
                passed_you.append(
                    {
                        "competitor_id": cid_str,
                        "competitor_name": name,
                        "old_rank": old_rank,
                        "new_rank": new_rank,
                        "rank_delta": rank_delta,
                    }
                )

    # Sort outputs for readability
    rank_gainers.sort(key=lambda x: x["rank_delta"])  # most negative first
    rank_losers.sort(key=lambda x: -x["rank_delta"])  # most positive first
    share_gainers.sort(key=lambda x: -float(x.get("share_delta_pct") or 0.0))
    share_losers.sort(key=lambda x: float(x.get("share_delta_pct") or 0.0))
    review_gainers.sort(key=lambda x: -int(x.get("review_delta") or 0))

    # Build summary
    parts: List[str] = []

    has_relative_pass_event = bool(passed_you or you_passed)

    if not has_relative_pass_event:
        if rank_gainers:
            parts.append(f"{len(rank_gainers)} moved up in rank")
        if rank_losers:
            parts.append(f"{len(rank_losers)} moved down in rank")

    if share_gainers:
        top = share_gainers[0]
        parts.append(f"{top['competitor_name']} gained +{top['share_delta_pct']}pp share")
    if share_losers:
        top = share_losers[0]
        parts.append(f"{top['competitor_name']} lost {top['share_delta_pct']}pp share")

    if review_gainers:
        top = review_gainers[0]
        parts.append(f"{top['competitor_name']} gained +{top['review_delta']} reviews")

    if passed_you:
        top = passed_you[0]
        parts.append(f"{top['competitor_name']} passed you in rank")

    if you_passed:
        top = you_passed[0]
        parts.append(f"you passed {you_passed[0]['competitor_name']} in rank")

    if not parts:
        return None

    summary = "YOU vs THEM: " + "; ".join(parts) + "."

    return {
        "type": "market_movers",
        "severity": "info",
        "summary": summary,
        "details": {
            "passed_you": passed_you,
            "you_passed": you_passed,
            "rank_losers": rank_losers,
            "rank_gainers": rank_gainers,
            "share_losers": share_losers,
            "share_gainers": share_gainers,
            "review_gainers": review_gainers,
        },
    }
