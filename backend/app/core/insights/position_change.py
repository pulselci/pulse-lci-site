from __future__ import annotations

from typing import Any, Dict, List, Optional


def _find_owner_row(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for r in rows or []:
        if r.get("is_business") is True:
            return r
    return None


def build_position_change_insight(
    previous_sections: Dict[str, Any],
    current_sections: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Deterministic snapshot-to-snapshot insight:
    - Compare owner row between previous report and current report.
    - Fire if rank changed OR abs(share_delta_pct) >= 1.0 OR abs(review_delta) >= 10.
    - Severity: warning if rank worsened, info otherwise.
    """
    prev_rows = (((previous_sections or {}).get("share_of_voice") or {}).get("rows") or [])
    curr_rows = (((current_sections or {}).get("share_of_voice") or {}).get("rows") or [])

    prev_owner = _find_owner_row(prev_rows)
    curr_owner = _find_owner_row(curr_rows)

    if not prev_owner or not curr_owner:
        return None

    old_rank = prev_owner.get("rank")
    new_rank = curr_owner.get("rank")

    old_share = prev_owner.get("share_pct")
    new_share = curr_owner.get("share_pct")

    old_reviews = prev_owner.get("reviews_total")
    new_reviews = curr_owner.get("reviews_total")

    if old_rank is None or new_rank is None:
        return None
    if old_share is None or new_share is None:
        return None
    if old_reviews is None or new_reviews is None:
        return None

    rank_delta = int(new_rank) - int(old_rank)          # negative means moved UP (3 -> 2 => -1)
    share_delta_pct = float(new_share) - float(old_share)
    review_delta = int(new_reviews) - int(old_reviews)

    rank_changed = rank_delta != 0
    share_trigger = abs(share_delta_pct) >= 1.0
    reviews_trigger = abs(review_delta) >= 10

    if not (rank_changed or share_trigger or reviews_trigger):
        return None

    severity = "warning" if rank_delta > 0 else "info"

    if rank_changed:
        if rank_delta < 0:
            summary = f"YOU vs THEM: You moved up {abs(rank_delta)} position{'s' if abs(rank_delta) != 1 else ''} this period."
        else:
            summary = f"YOU vs THEM: You moved down {abs(rank_delta)} position{'s' if abs(rank_delta) != 1 else ''} this period."
    elif share_trigger:
        direction = "increased" if share_delta_pct > 0 else "decreased"
        summary = f"YOU vs THEM: Your share of voice {direction} by {abs(share_delta_pct):.1f} pp this period."
    else:
        direction = "gained" if review_delta > 0 else "lost"
        summary = f"YOU vs THEM: You {direction} {abs(review_delta)} reviews this period."

    return {
        "type": "position_change",
        "severity": severity,
        "summary": summary,
        "details": {
            "old_rank": int(old_rank),
            "new_rank": int(new_rank),
            "rank_delta": int(rank_delta),
            "share_delta_pct": round(float(share_delta_pct), 1),
            "review_delta": int(review_delta),
        },
    }
