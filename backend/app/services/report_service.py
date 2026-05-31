from uuid import UUID

from app.core.config import settings
from app.core.db import get_conn
from app.models.schemas import ReportRegisterIn, ReportOut, ReportLatestOut


def register_report(payload: ReportRegisterIn) -> ReportOut:
    bucket = payload.storage_bucket or settings.REPORTS_BUCKET
    path = payload.storage_path

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into public.reports
                  (business_id, period_start, period_end, title, status, storage_bucket, storage_path, summary)
                values
                  (%s, %s, %s, %s, 'registered', %s, %s, %s)
                returning id, business_id, period_start, period_end, title, status, storage_bucket, storage_path, summary, created_at
                """,
                (
                    payload.business_id,
                    payload.period_start,
                    payload.period_end,
                    payload.title,
                    bucket,
                    path,
                    payload.summary,
                ),
            )
            row = cur.fetchone()
            conn.commit()
            return ReportOut(**row)


def get_latest_report(business_id: UUID) -> ReportLatestOut:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, business_id, period_start, period_end, title, status,
                       storage_bucket, storage_path, summary, created_at
                from public.reports
                where business_id = %s
                order by created_at desc
                limit 1
                """,
                (business_id,),
            )
            row = cur.fetchone()
            if not row:
                return ReportLatestOut(report=None, signed_url_placeholder=None)

            report = ReportOut(**row)

            placeholder = None
            if settings.SUPABASE_URL and report.storage_bucket and report.storage_path:
                placeholder = f"{settings.SUPABASE_URL}/storage/v1/object/public/{report.storage_bucket}/{report.storage_path}"

            return ReportLatestOut(report=report, signed_url_placeholder=placeholder)

from typing import List  # add at top if not already there
from uuid import UUID

from app.core.db import get_conn
from app.models.schemas import ReportOut

def list_reports_by_business(business_id: UUID) -> List[ReportOut]:
    """
    List reports for a business (newest periods first) for MVP history UI.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    business_id,
                    period_start,
                    period_end,
                    title,
                    status,
                    storage_bucket,
                    storage_path,
                    summary,
                    created_at
                FROM public.reports
                WHERE business_id = %s
                ORDER BY period_start DESC, created_at DESC
                LIMIT 100
                """,
                (business_id,),
            )
            rows = cur.fetchall()

    return [ReportOut(**r) for r in rows]


def get_report_by_id(report_id: UUID) -> ReportOut | None:
    """
    Fetch a single report by id for the MVP frontend.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    business_id,
                    period_start,
                    period_end,
                    title,
                    status,
                    storage_bucket,
                    storage_path,
                    summary,
                    created_at
                FROM public.reports
                WHERE id = %s
                """,
                (report_id,),
            )
            row = cur.fetchone()

    return ReportOut(**row) if row else None


