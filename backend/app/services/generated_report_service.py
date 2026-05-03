from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any
from uuid import UUID

from app.core.db import get_conn


def _jsonable(obj: Any) -> Any:
    """
    Recursively make obj JSON-serializable.
    - UUID -> str
    - datetime -> ISO string
    - dict/list -> recursively converted
    - other unknown types -> str()
    """
    if obj is None:
        return None

    if isinstance(obj, UUID):
        return str(obj)

    if isinstance(obj, datetime):
        return obj.isoformat()

    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}

    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]

    # int/float/str/bool are already fine
    if isinstance(obj, (str, int, float, bool)):
        return obj

    # fallback (Decimal, numpy types, etc)
    return str(obj)


def _as_date(v: Any) -> Any:
    """
    If v is a datetime, return v.date(). Otherwise return v unchanged.
    This prevents FastAPI/Pydantic from complaining when a response model uses `date`.
    """
    if isinstance(v, datetime):
        return v.date()
    return v


def insert_generated_report(
    business_id: UUID,
    schedule_id: UUID,
    period_start: datetime | date,
    period_end: datetime | date,
    status: str,
    title: str,
    summary_text: str,
    sections: dict | None,
    inputs: dict | None,
    error: Any | None,
):
    sections_safe = _jsonable(sections or {})
    inputs_safe = _jsonable(inputs or {})
    error_safe = _jsonable(error)

    # We send JSON strings and cast to jsonb in SQL for reliability.
    sections_json = json.dumps(sections_safe, ensure_ascii=False)
    inputs_json = json.dumps(inputs_safe, ensure_ascii=False)

    # If error is structured, store as JSON; otherwise store as plain text/null.
    error_json = (
        json.dumps(error_safe, ensure_ascii=False)
        if isinstance(error_safe, (dict, list))
        else error_safe
    )

    sql = """
    insert into generated_reports (
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
    )
    values (
      %s, %s, %s, %s, now() at time zone 'utc', %s, %s, %s, %s::jsonb, %s::jsonb, %s
    )
    returning
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
      error;
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    str(business_id),
                    str(schedule_id),
                    period_start,
                    period_end,
                    status,
                    title,
                    summary_text,
                    sections_json,
                    inputs_json,
                    error_json,
                ),
            )
            row = cur.fetchone()
        conn.commit()

    return dict(row) if row else None

from uuid import UUID

def list_reports_for_business(business_id: UUID, limit: int = 50) -> list[dict]:
    sql = """
    select
      id,
      business_id,
      report_type,
      period_start,
      period_end,
      payload,
      created_at
    from generated_reports
    where business_id = %s
    order by created_at desc
    limit %s;
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (business_id, limit))
            rows = cur.fetchall()

    # Return as dicts to match how your other endpoints serialize
    return [
        {
            "id": r[0],
            "business_id": r[1],
            "report_type": r[2],
            "period_start": r[3],
            "period_end": r[4],
            "payload": r[5],
            "created_at": r[6],
        }
        for r in rows
    ]


def list_reports_for_business(business_id: UUID, limit: int = 20):
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
    where business_id = %s
    order by generated_at desc
    limit %s;
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(business_id), int(limit)))
            rows = cur.fetchall()

    reports = [dict(r) for r in rows]

    # normalize date fields + created_at for response models
    for r in reports:
        r["period_start"] = _as_date(r.get("period_start"))
        r["period_end"] = _as_date(r.get("period_end"))
        if "created_at" not in r or r.get("created_at") is None:
            r["created_at"] = r.get("generated_at")

        # normalize JSON fields if stored as strings
        for k in ("sections", "inputs"):
            v = r.get(k)
            if isinstance(v, str):
                try:
                    r[k] = json.loads(v)
                except Exception:
                    pass

    return reports


def get_report_by_id(report_id: UUID):
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
    where id = %s;
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(report_id),))
            row = cur.fetchone()

    if row is None:
        return None

    report = dict(row)

    report["period_start"] = _as_date(report.get("period_start"))
    report["period_end"] = _as_date(report.get("period_end"))
    if "created_at" not in report or report.get("created_at") is None:
        report["created_at"] = report.get("generated_at")

    for k in ("sections", "inputs"):
        v = report.get(k)
        if isinstance(v, str):
            try:
                report[k] = json.loads(v)
            except Exception:
                pass

    return report


def get_latest_report_for_business(business_id: UUID):
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
    where business_id = %s
    order by generated_at desc
    limit 1;
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(business_id),))
            row = cur.fetchone()
            if row is None:
                return None

            try:
                report = dict(row)
            except Exception:
                cols = [d.name for d in cur.description]
                report = dict(zip(cols, row))

    # normalize period_start/period_end to pure dates (if your response model wants date)
    report["period_start"] = _as_date(report.get("period_start"))
    report["period_end"] = _as_date(report.get("period_end"))

    # your response model expects created_at; map it from generated_at
    if "created_at" not in report or report.get("created_at") is None:
        report["created_at"] = report.get("generated_at")

    # Normalize JSON fields if stored as strings
    for k in ("sections", "inputs"):
        v = report.get(k)
        if isinstance(v, str):
            try:
                report[k] = json.loads(v)
            except Exception:
                pass

    return report
