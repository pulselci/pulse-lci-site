from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.services.review_batch import ingest_reviews_for_business, get_competitors_with_place_ids
from app.services.review_analysis import extract_phrases_by_sentiment
from app.core.db import get_conn

router = APIRouter(tags=["review-ingestion"])


@router.post("/business/{business_id}/reviews/ingest")
def ingest_business_reviews(business_id: str):
    try:
        result = ingest_reviews_for_business(business_id)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to ingest reviews: {exc}")


@router.get("/business/{business_id}/reviews/debug")
def debug_review_ingestion(business_id: str):
    """Diagnose why perception/friction sections may be empty."""
    competitors = get_competitors_with_place_ids(business_id)

    review_counts = []
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select competitor_id, count(*) as total,
                       sum(case when review_text is not null and length(trim(review_text)) > 0 then 1 else 0 end) as with_text
                from public.google_reviews
                where business_id = %s
                group by competitor_id
                """,
                (business_id,),
            )
            for row in cur.fetchall():
                review_counts.append(dict(row))

    # Pull sample review texts per competitor for phrase-match diagnosis
    review_samples = []
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select competitor_id, review_text, rating
                from public.google_reviews
                where business_id = %s
                  and review_text is not null
                  and length(trim(review_text)) > 0
                order by competitor_id, created_at desc nulls last
                """,
                (business_id,),
            )
            for row in cur.fetchall():
                text = row.get("review_text") or ""
                praise = extract_phrases_by_sentiment([{"review_text": text}], "praise")
                complaint = extract_phrases_by_sentiment([{"review_text": text}], "complaint")
                review_samples.append({
                    "competitor_id": str(row.get("competitor_id")),
                    "rating": row.get("rating"),
                    "text_preview": text[:200],
                    "matched_praise": praise,
                    "matched_complaints": complaint,
                })

    # Also show which competitor is the owner
    owner_info = []
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, name, is_business, google_place_id
                from public.competitors
                where business_id = %s
                order by is_business desc, name
                """,
                (business_id,),
            )
            for row in cur.fetchall():
                owner_info.append(dict(row))

    # Show last sync errors per competitor
    sync_errors = []
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select competitor_id, last_error, last_succeeded_at, last_attempted_at
                from public.google_review_sync_state
                where business_id = %s
                order by last_attempted_at desc nulls last
                """,
                (business_id,),
            )
            for row in cur.fetchall():
                sync_errors.append(dict(row))

    return {
        "competitors_with_place_ids": competitors,
        "google_reviews_rows": review_counts,
        "total_with_text": sum(r.get("with_text", 0) for r in review_counts),
        "competitors_meta": owner_info,
        "sync_state": sync_errors,
        "review_samples": review_samples,
    }