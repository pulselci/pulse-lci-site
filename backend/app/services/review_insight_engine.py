from __future__ import annotations

from typing import Any, Dict, List

from app.core.db import get_conn
from app.services.review_analysis import (
    build_complaint_themes_insight,
    build_hidden_opportunity_insight,
    build_messaging_mismatch_insight,
    build_praise_themes_insight,
)


def get_competitors_with_reviews(business_id: str) -> List[Dict[str, Any]]:
    sql = """
    select
        gr.competitor_id as competitor_id,
        coalesce(c.name, 'Unknown Competitor') as competitor_name,
        count(*) as review_count
    from public.google_reviews gr
    join public.competitors c
      on c.id = gr.competitor_id
    where gr.competitor_id in (
        select id
        from public.competitors
        where business_id = %s
    )
    and gr.review_text is not null
    and length(trim(gr.review_text)) > 0
    group by gr.competitor_id, coalesce(c.name, 'Unknown Competitor')
    having count(*) > 0
    order by count(*) desc, coalesce(c.name, 'Unknown Competitor') asc
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (business_id,))
            rows = cur.fetchall()

    # hard dedupe guard
    seen: set[str] = set()
    cleaned: List[Dict[str, Any]] = []

    for row in rows:
        competitor_id = str(row.get("competitor_id"))
        if competitor_id in seen:
            continue
        seen.add(competitor_id)
        cleaned.append(row)

    return cleaned

def get_review_rows_for_business(business_id: str) -> List[Dict[str, Any]]:
    sql = """
    select
        gr.id as review_id,
        gr.business_id,
        gr.competitor_id,
        coalesce(c.name, 'Unknown Competitor') as competitor_name,
        gr.rating,
        gr.review_text,
        gr.created_at,
        case
            when c.is_business = true then true
            else false
        end as is_business
    from public.google_reviews gr
    left join public.competitors c
      on c.id = gr.competitor_id
    where lower(coalesce(c.name, 'Unknown Competitor')) in (
        select lower(c2.name)
        from public.competitors c2
        where c2.business_id = %s
    )
    and gr.review_text is not null
    and length(trim(gr.review_text)) > 0
    order by gr.created_at desc nulls last, gr.id desc
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (business_id,))
            rows = cur.fetchall()

    cleaned: List[Dict[str, Any]] = []
    for row in rows:
        cleaned.append(
            {
                "review_id": row.get("review_id"),
                "business_id": row.get("business_id"),
                "competitor_id": str(row.get("competitor_id")) if row.get("competitor_id") else None,
                "competitor_name": row.get("competitor_name") or "Unknown Competitor",
                "rating": row.get("rating"),
                "text": row.get("review_text") or "",
                "published_at": row.get("created_at"),
                "is_business": bool(row.get("is_business")),
            }
        )

    return cleaned

def build_review_insights_for_business(
    business_id: str,
    owner_competitor_id: str | None = None,
    owner_name: str | None = None,
) -> List[Dict[str, Any]]:
    competitors = get_competitors_with_reviews(business_id)
    insights: List[Dict[str, Any]] = []

    for row in competitors:
        competitor_id = str(row.get("competitor_id"))
        competitor_name = str(row.get("competitor_name") or "Competitor")

        # Skip self for competitor-facing insights
        if owner_competitor_id and competitor_id == owner_competitor_id:
            continue

        praise = build_praise_themes_insight(
            business_id=business_id,
            competitor_id=competitor_id,
            competitor_name=competitor_name,
        )
        if praise:
            insights.append(praise)

        complaints = build_complaint_themes_insight(
            business_id=business_id,
            competitor_id=competitor_id,
            competitor_name=competitor_name,
        )
        if complaints:
            insights.append(complaints)

        if owner_competitor_id and owner_name:
            hidden = build_hidden_opportunity_insight(
                business_id=business_id,
                owner_competitor_id=owner_competitor_id,
                owner_name=owner_name,
                competitor_id=competitor_id,
                competitor_name=competitor_name,
            )
            if hidden:
                insights.append(hidden)

        messaging = build_messaging_mismatch_insight(
            business_id=business_id,
            competitor_id=competitor_id,
            competitor_name=competitor_name,
            website_text="quality repairs, trusted local service, easy scheduling",
        )
        if messaging:
            insights.append(messaging)

    return insights