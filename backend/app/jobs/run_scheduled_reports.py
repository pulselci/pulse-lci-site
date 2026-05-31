from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.api.generated_reports import SendReportRequest, send_generated_report_email
from app.api.routes import generate_business_report
from app.core.db import get_conn
from zoneinfo import ZoneInfo


def _fetch_due_schedules(now: datetime) -> list[dict]:
    sql = """
    select
        rs.id,
        rs.business_id,
        rs.frequency,
        rs.day_of_month,
        rs.next_run_at
    from report_schedules rs
    join businesses b
      on b.id = rs.business_id
    where rs.next_run_at is not null
      and rs.next_run_at <= %s
      and coalesce(b.is_active, false) = true
    order by rs.next_run_at asc
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (now,))
            return [dict(r) for r in cur.fetchall()]


def _fetch_schedule_recipients(business_id: UUID) -> list[dict]:
    sql = """
    select email
    from report_recipients
    where business_id = %s
      and is_enabled = true
    order by email asc
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(business_id),))
            return [dict(r) for r in cur.fetchall()]


def _mark_schedule_run(schedule: dict, now: datetime) -> None:
    """
    Advance next_run_at using the schedule's configured monthly cadence,
    honoring the schedule's local timezone and storing the result in UTC.
    """
    schedule_id = UUID(str(schedule["id"]))
    frequency = str(schedule.get("frequency") or "monthly").strip().lower()
    day_of_month = int(schedule.get("day_of_month") or 1)
    hour = int(schedule.get("hour") or 9)
    minute = int(schedule.get("minute") or 0)
    is_enabled = bool(schedule.get("is_enabled", True))
    timezone_name = str(schedule.get("timezone") or "America/New_York").strip()

    if not is_enabled:
        next_run_at_utc = None
    else:
        try:
            local_tz = ZoneInfo(timezone_name)
        except Exception:
            local_tz = ZoneInfo("America/New_York")

        base = schedule.get("next_run_at") or now
        if not isinstance(base, datetime):
            base = now

        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)

        base_local = base.astimezone(local_tz)

        year = base_local.year
        month = base_local.month + 1
        if month == 13:
            month = 1
            year += 1

        import calendar
        last_day = calendar.monthrange(year, month)[1]
        run_day = min(day_of_month, last_day)

        next_run_local = datetime(
            year,
            month,
            run_day,
            hour,
            minute,
            0,
            tzinfo=local_tz,
        )

        if frequency != "monthly":
            pass

        next_run_at_utc = next_run_local.astimezone(timezone.utc)

    sql = """
    update report_schedules
    set
        last_run_at = %s,
        next_run_at = %s,
        updated_at = now()
    where id = %s
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (now, next_run_at_utc, str(schedule_id)))
        conn.commit()


def _collect_all_snapshots() -> None:
    """Collect a fresh snapshot for every business before running reports."""
    from app.services.snapshot_service import collect_snapshots_for_business

    sql = "SELECT DISTINCT business_id FROM competitors"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()

    business_ids = [row["business_id"] for row in rows]
    print(f"[scheduler] collecting snapshots for {len(business_ids)} businesses")

    for biz_id in business_ids:
        try:
            collect_snapshots_for_business(UUID(str(biz_id)))
            print(f"[scheduler] snapshot ok: {biz_id}")
        except Exception as e:
            print(f"[scheduler] snapshot failed: {biz_id}: {e}")


def run_scheduled_reports() -> None:
    now = datetime.now(timezone.utc)

    # Always collect fresh snapshots first so deltas are up to date
    try:
        _collect_all_snapshots()
    except Exception as e:
        print(f"[scheduler] snapshot collection error: {e}")

    schedules = _fetch_due_schedules(now)

    print(f"[scheduler] now={now.isoformat()} due_schedules={len(schedules)}")

    for schedule in schedules:
        schedule_id = schedule["id"]
        business_id = schedule["business_id"]

        print(f"[scheduler] processing schedule={schedule_id} business={business_id}")

        recipients = _fetch_schedule_recipients(UUID(str(business_id)))
        if not recipients:
            print(f"[scheduler] no recipients for schedule={schedule_id}, skipping")
            _mark_schedule_run(schedule, now)
            continue

        report = generate_business_report(UUID(str(business_id)))

        if hasattr(report, "model_dump"):
            report = report.model_dump()
        elif hasattr(report, "dict"):
            report = report.dict()

        if not isinstance(report, dict) or not report.get("id"):
            raise RuntimeError(f"Report generation did not return a valid report dict for business {business_id}")

        report_id = UUID(str(report["id"]))

        for recipient in recipients:
            to_email = str(recipient["email"]).strip()
            if not to_email:
                continue

            print(f"[scheduler] sending report={report_id} to={to_email}")
            send_generated_report_email(
                report_id,
                SendReportRequest(to_email=to_email),
            )

        _mark_schedule_run(schedule, now)
        print(f"[scheduler] completed schedule={schedule_id}")


if __name__ == "__main__":
    run_scheduled_reports()