"""
Outreach approval queue API.

Endpoints used by the approval UI to list, edit, approve, skip, and send
cold outreach emails to discovered prospects.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from app.core.db import get_conn
from app.services.email_service import send_plain_email

router = APIRouter(prefix="/outreach", tags=["outreach"])

# Track discovery job status in memory
_discovery_status: dict = {"running": False, "last": None, "log": []}


def _run_discovery(city: str, state: str, categories: List[str]) -> None:
    """Run prospect discovery in a background thread."""
    global _discovery_status
    _discovery_status["running"] = True
    _discovery_status["log"] = [f"Starting discovery: {city}, {state} — {', '.join(categories)}"]

    try:
        # Ensure outreach module is importable
        backend_dir = Path(__file__).resolve().parent.parent.parent
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))

        from outreach.discover import discover
        discover(city=city, state=state, categories=categories)
        _discovery_status["last"] = f"Done — {city}, {state}: {', '.join(categories)}"
        _discovery_status["log"].append("Discovery completed successfully.")
    except Exception as e:
        _discovery_status["last"] = f"Error: {e}"
        _discovery_status["log"].append(f"Error: {e}")
    finally:
        _discovery_status["running"] = False


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ProspectOut(BaseModel):
    id: str
    business_name: str
    category: Optional[str]
    address: Optional[str]
    city: Optional[str]
    state: Optional[str]
    website: Optional[str]
    phone: Optional[str]
    contact_email: Optional[str]
    reviews_count: Optional[int]
    rating: Optional[float]
    top_competitor_name: Optional[str]
    top_competitor_reviews: Optional[int]
    draft_subject: Optional[str]
    draft_body: Optional[str]
    status: str
    created_at: str


class DraftUpdateIn(BaseModel):
    contact_email: Optional[str] = None
    draft_subject: Optional[str] = None
    draft_body: Optional[str] = None
    notes: Optional[str] = None


class DiscoverIn(BaseModel):
    city: str
    state: str
    categories: str  # comma-separated


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_admin_key(api_key: str | None) -> None:
    expected = os.getenv("ADMIN_API_KEY", "")
    if expected and api_key != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _get_prospect(prospect_id: str) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM outreach_prospects WHERE id = %s",
                (prospect_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Prospect not found")
            return dict(row)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/discover")
def start_discovery(body: DiscoverIn, background_tasks: BackgroundTasks) -> dict:
    """Trigger prospect discovery in the background."""
    if _discovery_status["running"]:
        raise HTTPException(status_code=409, detail="A discovery run is already in progress. Check /outreach/discover/status.")

    categories = [c.strip() for c in body.categories.split(",") if c.strip()]
    if not categories:
        raise HTTPException(status_code=400, detail="At least one category is required.")

    background_tasks.add_task(_run_discovery, city=body.city, state=body.state, categories=categories)
    return {"ok": True, "message": f"Discovery started for {body.city}, {body.state}. Check the queue in a few minutes."}


@router.get("/discover/status")
def discovery_status() -> dict:
    """Check whether a discovery run is in progress."""
    return {
        "running": _discovery_status["running"],
        "last": _discovery_status["last"],
        "log": _discovery_status["log"][-10:],
    }


@router.get("/queue")
def list_queue(status: str = "draft_ready", limit: int = 50) -> list[dict]:
    """List prospects in the approval queue."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, business_name, category, address, city, state,
                       website, phone, contact_email, reviews_count, rating,
                       top_competitor_name, top_competitor_reviews,
                       draft_subject, draft_body, status,
                       created_at::text
                FROM outreach_prospects
                WHERE status = %s
                ORDER BY reviews_count DESC NULLS LAST
                LIMIT %s
                """,
                (status, limit),
            )
            rows = cur.fetchall()
            return [dict(r) for r in rows]


@router.get("/stats")
def get_stats() -> dict:
    """Return counts by status."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT status, COUNT(*) as count
                FROM outreach_prospects
                GROUP BY status
                ORDER BY count DESC
                """
            )
            rows = cur.fetchall()
            return {r["status"]: r["count"] for r in rows}


@router.patch("/{prospect_id}/draft")
def update_draft(prospect_id: str, body: DraftUpdateIn) -> dict:
    """Edit a prospect's email, contact email, or notes before approving."""
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    set_clause = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [prospect_id]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE outreach_prospects SET {set_clause}, updated_at = NOW() WHERE id = %s",
                values,
            )
        conn.commit()
    return {"ok": True}


@router.post("/{prospect_id}/skip")
def skip_prospect(prospect_id: str) -> dict:
    """Mark a prospect as skipped."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE outreach_prospects SET status = 'skipped', updated_at = NOW() WHERE id = %s",
                (prospect_id,),
            )
        conn.commit()
    return {"ok": True}


@router.post("/{prospect_id}/approve")
def approve_and_send(prospect_id: str) -> dict:
    """
    Approve a prospect and immediately send the draft email.
    Requires contact_email and draft_body to be set.
    """
    prospect = _get_prospect(prospect_id)

    to_email = prospect.get("contact_email")
    subject = prospect.get("draft_subject") or f"Competitive snapshot for {prospect['business_name']}"
    body = prospect.get("draft_body")

    if not to_email:
        raise HTTPException(status_code=400, detail="No contact_email set — add one before approving")
    if not body:
        raise HTTPException(status_code=400, detail="No draft_body set")

    result = send_plain_email(
        to_email=to_email,
        subject=subject,
        body=body,
    )

    if not result.ok:
        raise HTTPException(status_code=500, detail=f"Email send failed: {result.error}")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE outreach_prospects
                SET status = 'sent', approved_at = NOW(), sent_at = NOW(), updated_at = NOW()
                WHERE id = %s
                """,
                (prospect_id,),
            )
        conn.commit()

    return {"ok": True, "sent_to": to_email}


@router.get("/all")
def list_all(limit: int = 200) -> list[dict]:
    """List all prospects across all statuses (for the full pipeline view)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, business_name, category, city, state, contact_email,
                       reviews_count, rating, status, created_at::text, sent_at::text
                FROM outreach_prospects
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]
