from __future__ import annotations

import sys
from uuid import UUID

from app.core.db import get_conn, close_pool


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: python -m app.jobs.set_competitor_place_id <competitor_id> <google_place_id>")
        return 2

    competitor_id = UUID(sys.argv[1])
    place_id = sys.argv[2].strip()

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update public.competitors
                    set google_place_id = %s
                    where id = %s
                    returning id, google_place_id
                    """,
                    (place_id, competitor_id),
                )
                row = cur.fetchone()
                conn.commit()

        if not row:
            print("No competitor found for that id.")
            return 1

        print("Updated:", row)
        return 0
    finally:
        close_pool()


if __name__ == "__main__":
    raise SystemExit(main())
