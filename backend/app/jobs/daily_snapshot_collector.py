"""
LCI Daily Snapshot Collector (Phase C1)

Entry point for the daily snapshot automation job.
This file is intentionally thin:
- parse args
- resolve observed date
- call orchestrator
- exit with proper status for schedulers
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date
from typing import Optional
from app.core.db import close_pool


@dataclass
class JobResult:
    businesses: int = 0
    competitors: int = 0
    inserted: int = 0
    skipped: int = 0
    failed: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run LCI daily snapshot collector"
    )
    parser.add_argument(
        "--observed-date",
        help="Override observed date (YYYY-MM-DD). Default: today (UTC).",
        default=None,
    )
    parser.add_argument(
        "--dry-run",
        help="Collect data but do NOT write snapshots to DB.",
        action="store_true",
    )
    return parser.parse_args()


def resolve_observed_date(value: Optional[str]) -> date:
    if value is None:
        return date.today()

    try:
        return date.fromisoformat(value)
    except ValueError:
        raise SystemExit(
            f"Invalid --observed-date '{value}'. Expected YYYY-MM-DD."
        )


def main() -> int:
    args = parse_args()
    observed_date = resolve_observed_date(args.observed_date)

    from app.jobs.collectors.daily_snapshots import (
        run_daily_snapshot_collection,
    )
    from app.core.db import close_pool

    try:
        result: JobResult = run_daily_snapshot_collection(
            observed_date=observed_date,
            dry_run=bool(args.dry_run),
        )

        print(
            "[LCI Daily Collector] "
            f"businesses={result.businesses} "
            f"competitors={result.competitors} "
            f"inserted={result.inserted} "
            f"skipped={result.skipped} "
            f"failed={result.failed}"
        )

        return 1 if result.failed > 0 else 0

    finally:
        # Ensure psycopg_pool background threads stop cleanly
        close_pool()


    print(
        "[LCI Daily Collector] "
        f"businesses={result.businesses} "
        f"competitors={result.competitors} "
        f"inserted={result.inserted} "
        f"skipped={result.skipped} "
        f"failed={result.failed}"
    )

    # Non-zero exit code if anything failed
    return 1 if result.failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
