from __future__ import annotations

from typing import List
from uuid import UUID

from app.core.db import get_conn


def upsert_recipients_for_business(business_id: UUID, emails: List[str]) -> List[dict]:
    if not emails:
        return []

    rows_out = []

    with get_conn() as conn:
        with conn.cursor() as cur:
            for email in emails:
                cur.execute(
                    """
                    insert into public.report_recipients (business_id, email)
                    values (%s, %s)
                    on conflict (business_id, email)
                    do update set is_enabled = true
                    returning id, business_id, email, is_enabled, created_at
                    """,
                    (str(business_id), email),
                )
                row = cur.fetchone()
                if row:
                    rows_out.append(row)

        conn.commit()

    return rows_out


def list_recipients_for_business(business_id: UUID) -> List[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, business_id, email, is_enabled, created_at
                from public.report_recipients
                where business_id = %s
                order by created_at
                """,
                (str(business_id),),
            )
            return cur.fetchall()