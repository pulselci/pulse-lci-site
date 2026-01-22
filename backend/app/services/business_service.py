from typing import List
from uuid import UUID

from psycopg import IntegrityError

from app.core.db import get_conn
from app.models.schemas import (
    BusinessIntakeIn,
    BusinessOut,
    CompetitorOut,
    BusinessWithCompetitorsOut,
)


def create_business_and_competitors(payload: BusinessIntakeIn) -> BusinessWithCompetitorsOut:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into public.businesses (name, primary_domain, city, state, country, notes)
                values (%s, %s, %s, %s, %s, %s)
                returning id, name, primary_domain, city, state, country, notes, created_at
                """,
                (
                    payload.business_name,
                    payload.primary_domain,
                    payload.city,
                    payload.state,
                    payload.country,
                    payload.notes,
                ),
            )
            biz_row = cur.fetchone()
            business = BusinessOut(**biz_row)

            competitors_out: list[CompetitorOut] = []
            for c in payload.competitors:
                try:
                    cur.execute(
                        """
                        insert into public.competitors
                          (business_id, name, website_url, google_place_id, google_maps_url)
                        values (%s, %s, %s, %s, %s)
                        returning id, business_id, name, website_url, google_place_id, google_maps_url, created_at
                        """,
                        (
                            business.id,
                            c.name,
                            str(c.website_url) if c.website_url else None,
                            c.google_place_id,
                            str(c.google_maps_url) if c.google_maps_url else None,
                        ),
                    )
                    competitors_out.append(CompetitorOut(**cur.fetchone()))
                except IntegrityError:
                    conn.rollback()
                    # Skip duplicates in MVP
                    with conn.cursor() as cur2:
                        cur2.execute("select 1;")
                    continue

            conn.commit()
            return BusinessWithCompetitorsOut(business=business, competitors=competitors_out)


def get_business_with_competitors(business_id: UUID) -> BusinessWithCompetitorsOut | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, name, primary_domain, city, state, country, notes, created_at
                from public.businesses
                where id = %s
                """,
                (business_id,),
            )
            biz = cur.fetchone()
            if not biz:
                return None

            cur.execute(
                """
                select id, business_id, name, website_url, google_place_id, google_maps_url, created_at
                from public.competitors
                where business_id = %s
                order by created_at asc
                """,
                (business_id,),
            )
            competitors = [CompetitorOut(**r) for r in cur.fetchall()]

            return BusinessWithCompetitorsOut(business=BusinessOut(**biz), competitors=competitors)


def list_businesses() -> List[BusinessOut]:
    """
    Returns businesses for browsing in the MVP frontend.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, name, primary_domain, city, state, country, notes, created_at
                from public.businesses
                order by created_at desc
                limit 200
                """
            )
            rows = cur.fetchall()

    # Your cursor is returning dict-like rows already (since BusinessOut(**biz_row) works above),
    # so we can use the same pattern here:
    return [BusinessOut(**r) for r in rows]
