"""
Prospect onboarding pipeline.

Given a form submission (name, email, business name, city, state,
and up to 3 competitor names), this service:

  1. Resolves Google Place IDs for the business + each competitor
  2. Creates the business + competitor records in the DB
  3. Collects an initial snapshot (current rating + review count)
  4. Ingests the most recent reviews (for perception analysis)
  5. Generates a first report
  6. Emails the report as a PDF to the prospect

The first report gracefully omits metrics that require 30 days of
snapshot history (reviews_delta_30d). Everything else — share of voice,
competitive rankings, customer perception, review text themes — is
fully accurate from day one.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from app.models.schemas import BusinessIntakeIn, CompetitorIn
from app.services.business_service import create_business_and_competitors
from app.services.place_resolver import resolve_place_id
from app.services.report_schedule_service import upsert_schedule_for_business
from app.services.review_batch import ingest_reviews_for_business
from app.services.snapshot_service import collect_snapshots_for_business

logger = logging.getLogger(__name__)


def _find_existing_business(business_name: str, city: str, state: str) -> Optional[UUID]:
    """
    Look for an existing business record with the same name + city + state.
    Returns the UUID if found, None otherwise.
    This prevents duplicate records when a free-report prospect subscribes.
    """
    from app.core.db import get_conn
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id FROM businesses
                    WHERE lower(trim(name)) = lower(trim(%s))
                      AND lower(trim(city)) = lower(trim(%s))
                      AND lower(trim(state)) = lower(trim(%s))
                    ORDER BY created_at ASC
                    LIMIT 1
                    """,
                    (business_name, city, state),
                )
                row = cur.fetchone()
                if row:
                    return UUID(str(row["id"]))
    except Exception as exc:
        logger.warning("_find_existing_business error: %s", exc)
    return None



@dataclass
class OnboardingResult:
    ok: bool
    business_id: Optional[str] = None
    report_id: Optional[str] = None
    error: Optional[str] = None


def onboard_prospect(
    *,
    contact_name: str,
    contact_email: str,
    contact_phone: str = "",
    business_name: str,
    city: str,
    state: str,
    competitor_names: list[str],
    skip_report: bool = False,
) -> OnboardingResult:
    """
    Full onboarding pipeline for a new prospect.
    Set skip_report=True for paid subscribers — the webhook handles report generation
    after payment confirms so we don't send a blurred free-preview report by mistake.
    Safe to call in a background thread — all exceptions are caught and logged.
    """
    try:
        logger.info(
            "Starting prospect onboarding: business=%r city=%r state=%r competitors=%r",
            business_name, city, state, competitor_names,
        )

        # ------------------------------------------------------------------
        # 1. Resolve Place IDs
        # ------------------------------------------------------------------
        business_place = resolve_place_id(business_name, city, state)
        if not business_place:
            logger.warning(
                "Could not resolve Place ID for business %r — continuing without it",
                business_name,
            )

        competitors_in: list[CompetitorIn] = []

        # The client's own business as a competitor (is_business=True)
        competitors_in.append(
            CompetitorIn(
                name=business_name,
                google_place_id=business_place.place_id if business_place else None,
                google_maps_url=business_place.google_maps_url if business_place else None,
                is_business=True,
            )
        )

        # Competitors
        for comp_name in competitor_names:
            comp_name = comp_name.strip()
            if not comp_name:
                continue
            comp_place = resolve_place_id(comp_name, city, state)
            if not comp_place:
                logger.warning(
                    "Could not resolve Place ID for competitor %r — adding without it",
                    comp_name,
                )
            competitors_in.append(
                CompetitorIn(
                    name=comp_name,
                    google_place_id=comp_place.place_id if comp_place else None,
                    google_maps_url=comp_place.google_maps_url if comp_place else None,
                    is_business=False,
                )
            )

        # ------------------------------------------------------------------
        # 2. Create business + competitor records (or reuse existing match)
        # ------------------------------------------------------------------
        notes = (
            f"Free report prospect. Contact: {contact_name} "
            f"<{contact_email}> {contact_phone}".strip()
        )

        # Check for existing business with same name + city to avoid duplicates
        existing_business_id = _find_existing_business(business_name, city, state)
        if existing_business_id:
            business_id = existing_business_id
            logger.info("Reusing existing business %s for %r", business_id, business_name)
            # Update notes to record new contact
            try:
                from app.core.db import get_conn
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE businesses SET notes = %s WHERE id = %s",
                            (notes, str(business_id)),
                        )
                    conn.commit()
            except Exception as exc:
                logger.warning("Could not update notes for existing business: %s", exc)
        else:
            intake = BusinessIntakeIn(
                business_name=business_name,
                city=city,
                state=state,
                country="US",
                notes=notes,
                competitors=competitors_in,
            )
            result = create_business_and_competitors(intake)
            business_id: UUID = result.business.id
            logger.info("Created business %s for prospect %r", business_id, business_name)

        # ------------------------------------------------------------------
        # 3. Collect initial snapshots (current rating + review count)
        # ------------------------------------------------------------------
        try:
            collect_snapshots_for_business(business_id)
            logger.info("Snapshots collected for %s", business_id)
        except Exception as exc:
            logger.warning("Snapshot collection failed for %s: %s", business_id, exc)

        # ------------------------------------------------------------------
        # 4. Ingest reviews (customer perception text)
        # ------------------------------------------------------------------
        try:
            ingest_reviews_for_business(str(business_id))
            logger.info("Reviews ingested for %s", business_id)
        except Exception as exc:
            logger.warning("Review ingestion failed for %s: %s", business_id, exc)

        # ------------------------------------------------------------------
        # 5. Ensure a schedule record exists (required by generated_reports FK)
        # ------------------------------------------------------------------
        try:
            upsert_schedule_for_business(
                business_id,
                frequency="monthly",
                day_of_week=None,
                day_of_month=1,
                hour=8,
                minute=0,
                timezone="America/New_York",
                is_enabled=False,   # disabled until they become a paying client
                next_run_at=None,
            )
            logger.info("Schedule upserted for %s", business_id)
        except Exception as exc:
            logger.warning("Schedule upsert failed for %s: %s", business_id, exc)

        # ------------------------------------------------------------------
        # 6–8. Generate, mark, and email report (skipped for paid subscribers —
        #       the Stripe webhook handles this after payment confirms)
        # ------------------------------------------------------------------
        report_id: Optional[str] = None
        if not skip_report:
            try:
                from app.api.routes import generate_business_report
                report = generate_business_report(business_id)
                if hasattr(report, "model_dump"):
                    report = report.model_dump()
                elif hasattr(report, "dict"):
                    report = report.dict()
                report_id = str(report.get("id")) if isinstance(report, dict) else None
                logger.info("Report generated: %s for business %s", report_id, business_id)
            except Exception as exc:
                logger.error("Report generation failed for %s: %s", business_id, exc)

            if report_id:
                try:
                    from app.core.db import get_conn
                    with get_conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                UPDATE generated_reports
                                SET sections = sections || '{"is_free_preview": true}'::jsonb
                                WHERE id = %s
                                """,
                                (report_id,),
                            )
                        conn.commit()
                    logger.info("Marked report %s as free preview", report_id)
                except Exception as exc:
                    logger.warning("Could not mark report as free preview: %s", exc)

            if report_id and contact_email:
                try:
                    from app.api.generated_reports import send_generated_report_email, SendReportRequest
                    send_generated_report_email(
                        UUID(report_id),
                        SendReportRequest(
                            to_email=contact_email,
                            subject=f"Your Free Competitive Intelligence Report — {business_name}",
                            body_text=(
                                f"Hi {contact_name},\n\n"
                                "Attached is your free local competitor intelligence report. "
                                "It covers your current competitive position, review standings, "
                                "and the key opportunities we spotted in your market.\n\n"
                                "This is your baseline report. Each month you'll receive an updated "
                                "report showing exactly how your market is shifting.\n\n"
                                "Reply to this email if you have any questions.\n\n"
                                "— Pulse LCI"
                            ),
                        ),
                    )
                    logger.info("Report emailed to %s for business %s", contact_email, business_id)
                except Exception as exc:
                    logger.error("Email send failed for %s: %s", business_id, exc)
        else:
            logger.info("Skipping report generation for subscriber %s — webhook will handle it after payment", business_id)

        return OnboardingResult(
            ok=True,
            business_id=str(business_id),
            report_id=report_id,
        )

    except Exception as exc:
        logger.exception("Prospect onboarding failed for %r: %s", business_name, exc)
        return OnboardingResult(ok=False, error=str(exc))
