from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.services.analytics_service import compute_snapshot_deltas

router = APIRouter()


@router.get("/analytics/ping")
def analytics_ping():
    return {"ok": True}


@router.get("/snapshots/deltas")
def snapshots_deltas(business_id: UUID, days: int = 30):
    """
    Latest day snapshot per competitor + 1d/7d deltas (rating + review_count).
    """
    if days < 2 or days > 365:
        raise HTTPException(status_code=400, detail="days must be between 2 and 365")

    try:
        return compute_snapshot_deltas(business_id=business_id, days=days)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to compute deltas: {str(e)}")


@router.get("/insights")
def insights(business_id: UUID, days: int = 30):
    """
    Rules-based insights (no ML) computed from the latest-day deltas dataset.
    """
    # Use the same dataset (service) so this endpoint works even if we refactor API
    try:
        rows = compute_snapshot_deltas(business_id=business_id, days=days)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to compute insights: {str(e)}")

    if not rows:
        return {"business_id": str(business_id), "as_of": None, "insights": []}

    as_of = rows[0].get("observed_day_utc")

    # Identify "self" competitor (simple naming heuristic for now)
    self_row = None
    for r in rows:
        name = (r.get("competitor_name") or "").lower()
        if "self" in name:
            self_row = r
            break

    insights_list = []

    # Local averages
    ratings = [r["google_rating"] for r in rows if r.get("google_rating") is not None]
    reviews = [r["google_review_count"] for r in rows if r.get("google_review_count") is not None]

    avg_rating = (sum(ratings) / len(ratings)) if ratings else None
    avg_reviews = (sum(reviews) / len(reviews)) if reviews else None

    # Rank by rating (desc). Tie-breaker: review count.
    rows_by_rating = sorted(
        [r for r in rows if r.get("google_rating") is not None],
        key=lambda r: (r["google_rating"], r.get("google_review_count") or 0),
        reverse=True,
    )

    # Self vs local average rating + rank
    if self_row and avg_rating is not None and self_row.get("google_rating") is not None:
        diff = round(self_row["google_rating"] - avg_rating, 2)

        if diff < 0:
            insights_list.append(
                {
                    "type": "below_local_avg_rating",
                    "message": f"Your rating ({self_row['google_rating']}) is {abs(diff)} below the local average ({round(avg_rating, 2)}).",
                }
            )
        elif diff > 0:
            insights_list.append(
                {
                    "type": "above_local_avg_rating",
                    "message": f"Your rating ({self_row['google_rating']}) is {diff} above the local average ({round(avg_rating, 2)}).",
                }
            )
        else:
            insights_list.append(
                {
                    "type": "at_local_avg_rating",
                    "message": f"Your rating ({self_row['google_rating']}) matches the local average ({round(avg_rating, 2)}).",
                }
            )

        # 1-based rank
        rank = None
        for idx, r in enumerate(rows_by_rating, start=1):
            if r["competitor_id"] == self_row["competitor_id"]:
                rank = idx
                break

        if rank is not None:
            insights_list.append(
                {
                    "type": "rating_rank",
                    "message": f"You rank #{rank} of {len(rows_by_rating)} by Google rating.",
                }
            )

    # Most reviews (reputation volume)
    if reviews:
        top_reviews = max(rows, key=lambda r: r.get("google_review_count") or -1)
        insights_list.append(
            {
                "type": "most_reviews",
                "message": f"{top_reviews['competitor_name']} has the most reviews ({top_reviews['google_review_count']}).",
            }
        )

    # Fastest grower by 1-day delta (until 7d exists)
    growth_candidates = [r for r in rows if r.get("reviews_delta_1d") is not None]
    if growth_candidates:
        top_grower = max(growth_candidates, key=lambda r: r.get("reviews_delta_1d") or 0)
        if (top_grower.get("reviews_delta_1d") or 0) > 0:
            insights_list.append(
                {
                    "type": "fastest_grower_1d",
                    "message": f"{top_grower['competitor_name']} gained +{top_grower['reviews_delta_1d']} reviews since yesterday.",
                }
            )
        else:
            insights_list.append(
                {
                    "type": "no_review_movement_1d",
                    "message": "No competitors gained reviews since yesterday (based on collected snapshots).",
                }
            )

    # Optional: avg reviews (gives scale context)
    if avg_reviews is not None:
        insights_list.append(
            {
                "type": "local_avg_reviews",
                "message": f"Local average review count is {round(avg_reviews, 0)} reviews across tracked competitors.",
            }
        )

    return {"business_id": str(business_id), "as_of": as_of, "insights": insights_list}
