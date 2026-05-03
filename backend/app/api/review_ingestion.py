from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.services.review_batch import ingest_reviews_for_business

router = APIRouter(tags=["review-ingestion"])


@router.post("/business/{business_id}/reviews/ingest")
def ingest_business_reviews(business_id: str):
    try:
        result = ingest_reviews_for_business(business_id)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to ingest reviews: {exc}")