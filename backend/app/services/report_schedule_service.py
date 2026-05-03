from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from app.core.db import get_conn


def get_schedule_for_business(business_id: UUID) -> Optional[dict]:
    sql = """
    select
      id,
      business_id,
      frequency,
      day_of_week,
      day_of_month,
      hour,
      minute,
      timezone,
      is_enabled,
      last_run_at,
      next_run_at,
      created_at,
      updated_at
    from report_schedules
    where business_id = %s
    limit 1;
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(business_id),))
            row = cur.fetchone()
            return row


def upsert_schedule_for_business(
    business_id: UUID,
    *,
    frequency: str,
    day_of_week: Optional[int],
    day_of_month: Optional[int],
    hour: int,
    minute: int,
    timezone: str,
    is_enabled: bool,
    next_run_at: Optional[datetime],
) -> dict:
    sql = """
    insert into report_schedules (
      business_id, frequency, day_of_week, day_of_month, hour, minute, timezone, is_enabled, next_run_at
    )
    values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
    on conflict (business_id)
    do update set
      frequency = excluded.frequency,
      day_of_week = excluded.day_of_week,
      day_of_month = excluded.day_of_month,
      hour = excluded.hour,
      minute = excluded.minute,
      timezone = excluded.timezone,
      is_enabled = excluded.is_enabled,
      next_run_at = excluded.next_run_at,
      updated_at = now()
    returning
      id,
      business_id,
      frequency,
      day_of_week,
      day_of_month,
      hour,
      minute,
      timezone,
      is_enabled,
      last_run_at,
      next_run_at,
      created_at,
      updated_at;
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    str(business_id),
                    frequency,
                    day_of_week,
                    day_of_month,
                    hour,
                    minute,
                    timezone,
                    is_enabled,
                    next_run_at,
                ),
            )
            row = cur.fetchone()
            conn.commit()
            return row


def update_schedule_run_times(
    schedule_id: UUID,
    *,
    last_run_at: Optional[datetime],
    next_run_at: Optional[datetime],
) -> dict:
    sql = """
    update report_schedules
    set
      last_run_at = %s,
      next_run_at = %s,
      updated_at = now()
    where id = %s
    returning
      id,
      business_id,
      frequency,
      day_of_week,
      day_of_month,
      hour,
      minute,
      timezone,
      is_enabled,
      last_run_at,
      next_run_at,
      created_at,
      updated_at;
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (last_run_at, next_run_at, str(schedule_id)))
            row = cur.fetchone()
            conn.commit()
            return row


def find_due_schedules(now_utc: datetime) -> list[dict]:
    sql = """
    select
      id,
      business_id,
      frequency,
      day_of_week,
      day_of_month,
      hour,
      minute,
      timezone,
      is_enabled,
      last_run_at,
      next_run_at,
      created_at,
      updated_at
    from report_schedules
    where is_enabled = true
      and next_run_at is not null
      and next_run_at <= %s
    order by next_run_at asc;
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (now_utc,))
            rows = cur.fetchall()
            return rows or []
