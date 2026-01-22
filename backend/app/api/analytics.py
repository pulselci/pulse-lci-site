from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.core.db import get_conn

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

    sql = """
    with daily as (
      select
        s.business_id,
        s.competitor_id,
        date_trunc('day', s.observed_at) as day_utc,
        s.google_rating,
        s.google_review_count,
        row_number() over (
          partition by s.business_id, s.competitor_id, date_trunc('day', s.observed_at)
          order by s.created_at desc
        ) as rn
      from snapshots s
      where s.business_id = %s
        and s.observed_at >= (now() at time zone 'utc') - (%s::int || ' days')::interval
    ),
    d as (
      select
        business_id,
        competitor_id,
        day_utc,
        google_rating,
        google_review_count,
        lag(google_rating, 1) over (partition by business_id, competitor_id order by day_utc) as rating_1d_ago,
        lag(google_rating, 7) over (partition by business_id, competitor_id order by day_utc) as rating_7d_ago,
        lag(google_review_count, 1) over (partition by business_id, competitor_id order by day_utc) as reviews_1d_ago,
        lag(google_review_count, 7) over (partition by business_id, competitor_id order by day_utc) as reviews_7d_ago
      from daily
      where rn = 1
    ),
    latest_day as (
      select max(day_utc) as max_day from d
    )
    select
      d.business_id,
      d.competitor_id,
      c.name as competitor_name,
      d.day_utc as observed_day_utc,
      d.google_rating,
      d.google_review_count,
      (d.google_rating - d.rating_1d_ago) as rating_delta_1d,
      (d.google_rating - d.rating_7d_ago) as rating_delta_7d,
      (d.google_review_count - d.reviews_1d_ago) as reviews_delta_1d,
      (d.google_review_count - d.reviews_7d_ago) as reviews_delta_7d
    from d
    join latest_day ld on d.day_utc = ld.max_day
    join competitors c on c.id = d.competitor_id
    order by d.google_rating desc nulls last, d.google_review_count desc nulls last;
    """

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (str(business_id), days))
                rows = cur.fetchall()

        # Works for DictRow/RealDictCursor (row behaves like a mapping)
        return [dict(r) for r in rows]

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to compute deltas: {str(e)}")


@router.get("/insights")
def insights(business_id: UUID, days: int = 30):
    """
    Rules-based insights (no ML) computed from the latest-day deltas dataset.
    """
    rows = snapshots_deltas(business_id=business_id, days=days)
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
