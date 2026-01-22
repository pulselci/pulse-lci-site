from typing import List
from uuid import UUID

from psycopg import IntegrityError
from psycopg.types.json import Json

from app.core.db import get_conn
from app.models.schemas import (
    SnapshotBulkIn,
    SnapshotBulkOut,
    SnapshotListItemOut,
    SnapshotDetailOut,
)



def insert_snapshots_bulk(payload: SnapshotBulkIn) -> SnapshotBulkOut:
    inserted = 0
    skipped = 0

    with get_conn() as conn:
        with conn.cursor() as cur:
            for s in payload.snapshots:
                try:
                    cur.execute(
                        """
                        insert into public.snapshots (
                          business_id, competitor_id, observed_at,
                          google_rating, google_review_count,
                          offer_summary, price_hint,
                          visibility_score, notes, raw
                        )
                        values (%s,%s, coalesce(%s, now()),
                                %s,%s,
                                %s,%s,
                                %s,%s,%s)
                        """,
                        (
                            s.business_id,
                            s.competitor_id,
                            s.observed_at,
                            s.google_rating,
                            s.google_review_count,
                            s.offer_summary,
                            s.price_hint,
                            s.visibility_score,
                            s.notes,
                            Json(s.raw) if s.raw is not None else None,
                        ),
                    )
                    inserted += 1
                except IntegrityError:
                    conn.rollback()
                    skipped += 1
                    # Skip duplicates in MVP
                    with conn.cursor() as cur2:
                        cur2.execute("select 1;")
                    continue

            conn.commit()

    return SnapshotBulkOut(inserted=inserted, skipped_duplicates=skipped)


def list_snapshots_by_business(business_id: UUID) -> List[SnapshotListItemOut]:
    """
    Lightweight snapshot list for browsing in the MVP frontend.
    Includes competitor name + basic metrics for list display.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                s.id,
                s.business_id,
                s.competitor_id,
                c.name as competitor_name,
                s.created_at,
                s.observed_at as observed_at,
                s.google_rating,
                s.google_review_count
                from public.snapshots s
                join public.competitors c on c.id = s.competitor_id
                where s.business_id = %s
                order by s.observed_at desc, s.created_at desc
                limit 200
                """,
    (business_id,),
)

            rows = cur.fetchall()

    return [SnapshotListItemOut(**r) for r in rows]


def get_snapshot_by_id(snapshot_id: UUID) -> SnapshotDetailOut | None:
    """
    Full snapshot detail for clicking into a snapshot row in the frontend.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                  id,
                  business_id,
                  competitor_id,
                  observed_at,
                  created_at,
                  google_rating,
                  google_review_count,
                  offer_summary,
                  price_hint,
                  visibility_score,
                  notes,
                  raw
                from public.snapshots
                where id = %s
                """,
                (snapshot_id,),
            )
            row = cur.fetchone()
            if not row:
                return None

    # row is dict-like from your cursor config
    return SnapshotDetailOut(**row)
