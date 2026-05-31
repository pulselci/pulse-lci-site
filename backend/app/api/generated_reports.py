from __future__ import annotations

import json
from typing import Any, Dict
from uuid import UUID

from app.core.config import settings
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, EmailStr

from app.core.db import get_conn
from app.services.pdf_service import render_report_pdf
from app.services.email_service import send_report_email
from fastapi import Depends, Header, HTTPException

import os

def verify_admin_key(x_admin_key: str = Header(None)):
    expected = os.getenv("ADMIN_API_KEY")
    if not expected or x_admin_key != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")

router = APIRouter(tags=["generated-reports"])


class SendReportRequest(BaseModel):
    to_email: EmailStr
    subject: str | None = None
    body_text: str | None = None


def _normalize_report(d: Dict[str, Any]) -> Dict[str, Any]:
    """Parse JSON fields if they are stored as strings."""
    for k in ("sections", "inputs"):
        v = d.get(k)
        if isinstance(v, str):
            try:
                d[k] = json.loads(v)
            except Exception:
                pass
    return d


def _fetch_report(report_id: UUID) -> Dict[str, Any]:
    sql = """
    select
      id,
      business_id,
      schedule_id,
      period_start,
      period_end,
      generated_at,
      status,
      title,
      summary_text,
      sections,
      inputs,
      error
    from generated_reports
    where id = %s
    limit 1;
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(report_id),))
            row = cur.fetchone()

            if row is None:
                raise HTTPException(status_code=404, detail="generated_report not found")

            try:
                report = dict(row)
            except Exception:
                cols = [d.name for d in cur.description]
                report = dict(zip(cols, row))

    return _normalize_report(report)

def _fetch_business_name(business_id: UUID) -> str:
    sql = "select name from public.businesses where id = %s limit 1;"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(business_id),))
            row = cur.fetchone()
            if not row:
                return "business"
            try:
                return row["name"]
            except Exception:
                return row[0]


@router.get("/generated-reports/{report_id}/pdf")
def get_generated_report_pdf(report_id: UUID):
    report = _fetch_report(report_id)

    try:
        pdf_bytes = render_report_pdf(report)
    except Exception as e:
        raise HTTPException(
    status_code=500,
    detail=f"pdf_render_failed: {type(e).__name__}: {e}",
)

    business_name = _fetch_business_name(report["business_id"])
    safe_name = business_name.replace(" ", "_").replace("/", "-")
    period_end = report.get("period_end")
    period_str = period_end.date().isoformat() if period_end else "report"

    filename = f"{safe_name}_LCI_Report_{period_str}.pdf"

    headers = {"Content-Disposition": f'inline; filename="{filename}"'}

    return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)


@router.post("/generated-reports/{report_id}/send")
def send_generated_report_email(
    report_id: UUID,
    payload: SendReportRequest,
    admin_ok: None = Depends(verify_admin_key)
):
    report = _fetch_report(report_id)

    # Build PDF
    try:
        pdf_bytes = render_report_pdf(report)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"pdf_render_failed: {type(e).__name__}: {repr(e)}",
        )

    # Subject/body defaults
    subject = payload.subject or (report.get("title") or "Your LCI Report")
    body_text = (
        payload.body_text
        or "Attached is your latest LCI competitive intelligence report (PDF)."
    )

    business_name = _fetch_business_name(report["business_id"])
    safe_name = business_name.replace(" ", "_").replace("/", "-")
    period_end = report.get("period_end")
    period_str = period_end.date().isoformat() if period_end else "report"

    filename = f"{safe_name}_LCI_Report_{period_str}.pdf"
    
    result = send_report_email(
        to_email=str(payload.to_email),
        subject=subject,
        body_text=body_text,
        pdf_bytes=pdf_bytes,
        filename=filename,
        report_id=report_id,
        business_name=business_name,
        summary_text=report.get("summary_text"),
    )

    if not result.ok:
        raise HTTPException(
            status_code=500,
            detail=result.error or "Email send failed",
        )

    return {"ok": True, "error": None}
