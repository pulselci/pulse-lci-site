from __future__ import annotations

from uuid import UUID

from app.core.db import get_conn


def compute_snapshot_deltas(business_id: UUID, days: int = 30) -> list[dict]:
    """
    Latest day snapshot per competitor + 1d/7d deltas (rating + review_count).
    Also computes reviews_delta_30d by comparing current count to the oldest
    snapshot within the window (works even with sparse snapshots).
    Returns list[dict].
    """
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
        lag(google_review_count, 7) over (partition by business_id, competitor_id order by day_utc) as reviews_7d_ago,
        first_value(google_review_count) over (
          partition by business_id, competitor_id order by day_utc asc
          rows between unbounded preceding and unbounded following
        ) as reviews_period_start
      from daily
      where rn = 1
    ),
    latest_per_competitor as (
      select *
      from (
        select
          d.*,
          row_number() over (
            partition by d.business_id, d.competitor_id
            order by d.day_utc desc
          ) as rn_latest
        from d
      ) x
      where x.rn_latest = 1
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
      (d.google_review_count - d.reviews_7d_ago) as reviews_delta_7d,
      (d.google_review_count - d.reviews_period_start) as reviews_delta_30d
    from latest_per_competitor d
    join competitors c on c.id = d.competitor_id
    order by d.google_rating desc nulls last, d.google_review_count desc nulls last;
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(business_id), days))
            rows = cur.fetchall()

    return [dict(r) for r in rows]
