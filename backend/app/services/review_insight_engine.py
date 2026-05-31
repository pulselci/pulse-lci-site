from __future__ import annotations

import logging
import re
import urllib.request
from typing import Any, Dict, List, Optional

from app.core.db import get_conn

logger = logging.getLogger(__name__)
from app.services.review_analysis import (
    build_complaint_themes_insight,
    build_hidden_opportunity_insight,
    build_messaging_mismatch_insight,
    build_praise_themes_insight,
)


def fetch_website_text(url: str, max_chars: int = 3000) -> str:
    """
    Fetch a competitor's homepage and return visible text, stripped of HTML.
    Returns empty string on any error.
    """
    if not url or not url.strip():
        return ""
    try:
        if not url.startswith("http"):
            url = "https://" + url
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; LCIBot/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read(max_chars * 10).decode("utf-8", errors="ignore")
        # Strip tags, collapse whitespace
        text = re.sub(r"<style[^>]*>.*?</style>", " ", raw, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception as exc:
        logger.debug("website fetch failed for %s: %s", url, exc)
        return ""


def get_competitors_with_reviews(business_id: str) -> List[Dict[str, Any]]:
    sql = """
    select
        gr.competitor_id as competitor_id,
        coalesce(c.name, 'Unknown Competitor') as competitor_name,
        c.website_url as website_url,
        count(*) as ingested_review_count
    from public.google_reviews gr
    join public.competitors c
      on c.id = gr.competitor_id
    where gr.competitor_id in (
        select id from public.competitors where business_id = %s
    )
    and gr.review_text is not null
    and length(trim(gr.review_text)) > 0
    group by gr.competitor_id, coalesce(c.name, 'Unknown Competitor'), c.website_url
    having count(*) > 0
    order by coalesce(c.name, 'Unknown Competitor') asc
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
    where gr.business_id = %s
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
    competitor_review_totals: Optional[Dict[str, int]] = None,
) -> List[Dict[str, Any]]:
    competitors = get_competitors_with_reviews(business_id)
    insights: List[Dict[str, Any]] = []

    logger.info(
        "[review_insights] business_id=%s owner_competitor_id=%s competitors_with_reviews=%d",
        business_id, owner_competitor_id, len(competitors)
    )

    for row in competitors:
        competitor_id = str(row.get("competitor_id"))
        competitor_name = str(row.get("competitor_name") or "Competitor")

        # Skip self for competitor-facing insights
        if owner_competitor_id and competitor_id == owner_competitor_id:
            logger.info("[review_insights] skipping owner competitor_id=%s name=%s", competitor_id, competitor_name)
            continue

        logger.info("[review_insights] analyzing competitor_id=%s name=%s review_count=%s",
                    competitor_id, competitor_name, row.get("review_count"))

        # Fetch real website copy if URL is available
        website_url = row.get("website_url") or ""
        website_text = ""
        if website_url:
            website_text = fetch_website_text(website_url)
            logger.info("[review_insights] website fetch %s chars for %s",
                        len(website_text), competitor_name)

        reviews_total = int(
            (competitor_review_totals or {}).get(competitor_id)
            or row.get("reviews_total")
            or 0
        )

        praise = build_praise_themes_insight(
            business_id=business_id,
            competitor_id=competitor_id,
            competitor_name=competitor_name,
        )
        logger.info("[review_insights] praise insight for %s: %s", competitor_name, praise is not None)
        if praise:
            # Attach the real Google review total so formatter can sort by market size
            (praise.get("details") or {})["reviews_total"] = reviews_total
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

        if website_text:
            messaging = build_messaging_mismatch_insight(
                business_id=business_id,
                competitor_id=competitor_id,
                competitor_name=competitor_name,
                website_text=website_text,
            )
            if messaging:
                insights.append(messaging)

    return insights