# app/services/share_of_voice_service.py

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _to_int(x: Any, default: int = 0) -> int:
    try:
        if x is None:
            return default
        return int(x)
    except Exception:
        return default


def compute_share_of_voice_from_deltas(
    competitor_deltas: List[Dict[str, Any]],
    *,
    top_n: int = 10,
    include_business_self: bool = False,
    business_name: Optional[str] = None,
    business_reviews_total: Optional[int] = None,
    business_competitor_id: Optional[str] = None,  # ✅ NEW
) -> Dict[str, Any]:
    """
    Computes "share of voice" using current review totals from deltas.

    Enhancements:
      - Adds competitor_id to each returned row (if available)
      - Business row detection prefers competitor_id match (business_competitor_id)
      - Falls back to normalized name match (handles "Self")
      - If the detected business name ends with " Self", normalizes display to business_name
    """

    cleaned: List[Dict[str, Any]] = []
    for d in competitor_deltas or []:
        if not isinstance(d, dict):
            continue

        name = d.get("competitor_name") or d.get("name") or "Unknown"
        total_reviews = _to_int(d.get("google_review_count"), 0)

        # ✅ try multiple sources for competitor_id
        cid = d.get("competitor_id") or d.get("id")
        cid = str(cid) if cid is not None and str(cid).strip() != "" else None

        delta_7d = d.get("reviews_delta_7d")
        delta_7d_int = _to_int(delta_7d, 0) if delta_7d is not None else None

        delta_30d = d.get("reviews_delta_30d")
        delta_30d_int = _to_int(delta_30d, 0) if delta_30d is not None else None

        raw_rating = d.get("google_rating") or d.get("rating") or d.get("avg_rating")
        try:
            google_rating = round(float(raw_rating), 1) if raw_rating is not None else None
        except (TypeError, ValueError):
            google_rating = None

        cleaned.append(
            {
                "competitor_id": cid,
                "competitor_name": name,
                "reviews_total": total_reviews,
                "reviews_delta_7d": delta_7d_int,
                "reviews_delta_30d": delta_30d_int,
                "is_business": bool(d.get("is_business") or False),
                "google_rating": google_rating,
            }
        )

    def _norm(s: str) -> str:
        s = (s or "").strip().lower()
        if s.endswith(" self"):
            s = s[:-5].strip()
        return s

    # --- Business row detection / injection ---
    if include_business_self and (business_name or business_competitor_id):
        found = False

        # 1) Prefer: competitor_id match
        if business_competitor_id:
            bcid = str(business_competitor_id)
            for x in cleaned:
                if x.get("competitor_id") and str(x["competitor_id"]) == bcid:
                    x["is_business"] = True
                    # Normalize display name if it ends with " Self"
                    if business_name and (x.get("competitor_name") or "").strip().lower().endswith(" self"):
                        x["competitor_name"] = business_name
                    found = True
                    break

        # 2) Fallback: normalized name match (handles "Self")
        if (not found) and business_name:
            bn = business_name.strip().lower()
            for x in cleaned:
                n = _norm(x.get("competitor_name") or "")
                if n and n == bn:
                    x["is_business"] = True
                    if (x.get("competitor_name") or "").strip().lower().endswith(" self"):
                        x["competitor_name"] = business_name
                    found = True
                    break

        # 3) Fallback: inject business row if not found and count provided
        if (not found) and (business_reviews_total is not None) and business_name:
            cleaned.append(
                {
                    "competitor_id": str(business_competitor_id) if business_competitor_id else None,
                    "competitor_name": business_name,
                    "reviews_total": _to_int(business_reviews_total, 0),
                    "reviews_delta_7d": None,
                    "is_business": True,
                }
            )

    market_total = sum(x["reviews_total"] for x in cleaned)

    # For share change we compare:
    market_total_7d_ago = 0
    for x in cleaned:
        if x["reviews_delta_7d"] is None:
            market_total_7d_ago += x["reviews_total"]
        else:
            market_total_7d_ago += max(0, x["reviews_total"] - _to_int(x["reviews_delta_7d"], 0))

    # Sort by current total reviews desc
    cleaned.sort(key=lambda r: r["reviews_total"], reverse=True)

    rows: List[Dict[str, Any]] = []
    for i, x in enumerate(cleaned[:top_n], start=1):
        total = x["reviews_total"]
        share_now = (total / market_total * 100.0) if market_total > 0 else 0.0

        if market_total_7d_ago > 0:
            if x["reviews_delta_7d"] is None:
                total_7d_ago = total
            else:
                total_7d_ago = max(0, total - _to_int(x["reviews_delta_7d"], 0))
            share_7d_ago = (total_7d_ago / market_total_7d_ago * 100.0)
        else:
            share_7d_ago = share_now

        share_change = share_now - share_7d_ago

        row = {
            "rank": i,
            "competitor_id": x.get("competitor_id"),
            "competitor_name": x["competitor_name"],
            "reviews_total": total,
            "share_pct": round(share_now, 1),
            "share_change_7d_pct": round(share_change, 1),
            "google_rating": x.get("google_rating"),
            "reviews_delta_30d": x.get("reviews_delta_30d"),
        }
        if x.get("is_business"):
            row["is_business"] = True

        rows.append(row)

    return {
        "market_total_reviews": market_total,
        "rows": rows,
    }
