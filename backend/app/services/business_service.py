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
                            (business_id, name, website_url, google_place_id, google_maps_url, is_business)
                        values
                            (%s, %s, %s, %s, %s, %s)
                        returning id, business_id, name, website_url, google_place_id, google_maps_url, created_at, is_business
                        """,
                        (
                            str(business.id),
                            c.name,
                            c.website_url,
                            c.google_place_id,
                            c.google_maps_url,
                            bool(c.is_business),
                        ),
                    )
                    row = cur.fetchone()
                    competitors_out.append(CompetitorOut(**row))
                except IntegrityError:
                    conn.rollback()
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
                select id, business_id, name, website_url, google_place_id, google_maps_url, created_at, is_business
                from public.competitors
                where business_id = %s
                order by created_at, name
                """,
                (business_id,),
            )
            competitors = [CompetitorOut(**r) for r in cur.fetchall()]

            return BusinessWithCompetitorsOut(
                business=BusinessOut(**biz),
                competitors=competitors,
            )


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

    return [BusinessOut(**r) for r in rows]