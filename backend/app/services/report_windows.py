from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Tuple

from zoneinfo import ZoneInfo


def _to_local(dt_utc: datetime, tz: str) -> datetime:
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    return dt_utc.astimezone(ZoneInfo(tz))


def _to_utc(dt_local: datetime) -> datetime:
    if dt_local.tzinfo is None:
        raise ValueError("dt_local must be timezone-aware")
    return dt_local.astimezone(timezone.utc)


def previous_weekly_window(
    now_utc: datetime,
    *,
    tz: str,
    day_of_week: int,
    hour: int,
    minute: int,
) -> Tuple[datetime, datetime]:
    """
    Return (start_utc, end_utc) for the PREVIOUS full weekly window aligned to:
    local weekday (0=Mon..6=Sun), local hour/minute.

    Example: If schedule is Thu 09:00 ET, and now is any time after that,
    end_utc becomes the most recent Thu 09:00 ET, and start_utc is 7 days earlier.
    """
    local_now = _to_local(now_utc, tz)
    target = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # Python weekday: Monday=0..Sunday=6 (same as our stored day_of_week)
    days_back = (target.weekday() - day_of_week) % 7
    end_local = target - timedelta(days=days_back)

    # If we haven't reached today's scheduled time yet, step back one week
    if end_local > local_now:
        end_local = end_local - timedelta(days=7)

    start_local = end_local - timedelta(days=7)

    return _to_utc(start_local), _to_utc(end_local)


def previous_monthly_window(
    now_utc: datetime,
    *,
    tz: str,
    day_of_month: int,
    hour: int,
    minute: int,
) -> Tuple[datetime, datetime]:
    """
    Return (start_utc, end_utc) for the PREVIOUS full monthly window aligned to:
    local day_of_month (1..28/29/30/31), local hour/minute.
    If the requested day doesn't exist in a month, clamp to the last day.
    """
    local_now = _to_local(now_utc, tz)

    def last_day_of_month(year: int, month: int) -> int:
        if month == 12:
            next_month = datetime(year + 1, 1, 1, tzinfo=ZoneInfo(tz))
        else:
            next_month = datetime(year, month + 1, 1, tzinfo=ZoneInfo(tz))
        last = next_month - timedelta(days=1)
        return last.day

    def clamp_day(year: int, month: int, requested: int) -> int:
        return min(requested, last_day_of_month(year, month))

    # Candidate end in current month
    end_day = clamp_day(local_now.year, local_now.month, day_of_month)
    end_local = local_now.replace(
        day=end_day, hour=hour, minute=minute, second=0, microsecond=0
    )

    # If not reached the schedule moment yet, go to previous month
    if end_local > local_now:
        # move to previous month
        if local_now.month == 1:
            y, m = local_now.year - 1, 12
        else:
            y, m = local_now.year, local_now.month - 1
        end_day = clamp_day(y, m, day_of_month)
        end_local = datetime(y, m, end_day, hour, minute, 0, 0, tzinfo=ZoneInfo(tz))

    # Start is previous month boundary
    if end_local.month == 1:
        sy, sm = end_local.year - 1, 12
    else:
        sy, sm = end_local.year, end_local.month - 1

    start_day = clamp_day(sy, sm, day_of_month)
    start_local = datetime(sy, sm, start_day, hour, minute, 0, 0, tzinfo=ZoneInfo(tz))

    return _to_utc(start_local), _to_utc(end_local)
