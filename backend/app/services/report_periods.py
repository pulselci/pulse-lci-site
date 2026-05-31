from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Schedule:
    frequency: str  # "weekly" | "monthly"
    day_of_week: int | None  # 0=Mon..6=Sun (weekly)
    day_of_month: int | None  # 1..31 (monthly)
    hour: int
    minute: int
    timezone: str  # e.g. "America/New_York"


def _clamp_day(year: int, month: int, day: int) -> int:
    """Clamp day to last valid day of the month."""
    # Move to first day of next month, then back one day
    if month == 12:
        next_month = datetime(year + 1, 1, 1)
    else:
        next_month = datetime(year, month + 1, 1)
    last_day = (next_month - timedelta(days=1)).day
    return min(day, last_day)


def compute_next_run_at(schedule: Schedule, now_utc: datetime | None = None) -> datetime:
    """
    Returns the next run datetime in UTC.
    Uses schedule.timezone as the local wall-clock timezone.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    tz = ZoneInfo(schedule.timezone)
    now_local = now_utc.astimezone(tz)

    # target local time today
    target_local_time = (schedule.hour, schedule.minute)

    if schedule.frequency == "weekly":
        if schedule.day_of_week is None:
            raise ValueError("day_of_week is required for weekly schedules")

        # Compute days until next desired weekday
        # Python weekday(): Monday=0..Sunday=6
        today_wd = now_local.weekday()
        delta_days = (schedule.day_of_week - today_wd) % 7

        candidate_date = (now_local.date() + timedelta(days=delta_days))
        candidate_local = datetime(
            candidate_date.year,
            candidate_date.month,
            candidate_date.day,
            target_local_time[0],
            target_local_time[1],
            tzinfo=tz,
        )

        # If it's today but already past the target time, go to next week
        if candidate_local <= now_local:
            candidate_date = candidate_date + timedelta(days=7)
            candidate_local = datetime(
                candidate_date.year,
                candidate_date.month,
                candidate_date.day,
                target_local_time[0],
                target_local_time[1],
                tzinfo=tz,
            )

        return candidate_local.astimezone(timezone.utc)

    if schedule.frequency == "monthly":
        if schedule.day_of_month is None:
            raise ValueError("day_of_month is required for monthly schedules")

        year = now_local.year
        month = now_local.month
        day = _clamp_day(year, month, schedule.day_of_month)

        candidate_local = datetime(
            year,
            month,
            day,
            target_local_time[0],
            target_local_time[1],
            tzinfo=tz,
        )

        # If already passed this month, schedule next month
        if candidate_local <= now_local:
            if month == 12:
                year += 1
                month = 1
            else:
                month += 1
            day = _clamp_day(year, month, schedule.day_of_month)
            candidate_local = datetime(
                year,
                month,
                day,
                target_local_time[0],
                target_local_time[1],
                tzinfo=tz,
            )

        return candidate_local.astimezone(timezone.utc)

    raise ValueError(f"Unsupported frequency: {schedule.frequency}")
