from __future__ import annotations

from typing import List, Dict, Any

from app.core.db import get_conn
from app.services.review_ingestion import ingest_google_reviews_for_competitor


def get_competitors_with_place_ids(business_id: str) -> List[Dict[str, Any]]:
    sql = """
    select id, google_place_id
    from public.competitors
    where business_id = %s
      and google_place_id is not null
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (business_id,))
            rows = cur.fetchall()

    return [
        {
            "competitor_id": str(r["id"]),
            "google_place_id": r["google_place_id"],
        }
        for r in rows
    ]


def ingest_reviews_for_business(business_id: str) -> Dict[str, Any]:
    competitors = get_competitors_with_place_ids(business_id)

    results = []
    total_upserted = 0

    for comp in competitors:
        result = ingest_google_reviews_for_competitor(
            business_id=business_id,
            competitor_id=comp["competitor_id"],
            google_place_id=comp["google_place_id"],
        )

        results.append(result)

        if result.get("ok"):
            total_upserted += result.get("upserted", 0)

    return {
        "business_id": business_id,
        "competitors_processed": len(competitors),
        "total_upserted": total_upserted,
        "results": results,
    }