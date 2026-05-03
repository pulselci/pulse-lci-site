from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

import base64
import io

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

import math
import stripe
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.services.snapshot_service import collect_snapshots_for_business

from app.api.analytics import router as analytics_router
from app.api.generated_reports import (
    SendReportRequest,
    router as generated_reports_router,
    send_generated_report_email,
)
from app.api.report_schedules import router as report_schedules_router
from app.api.review_ingestion import router as review_ingestion_router
from app.core.config import settings
from app.core.db import get_conn
from app.core.insights.market_movers import build_market_movers_insight
from app.core.insights.position_change import build_position_change_insight
from app.models.schemas import (
    BusinessIntakeIn,
    BusinessOut,
    BusinessWithCompetitorsOut,
    CompetitorIn,
    GeneratedReportOut,
    OnboardingIn,
    OnboardingOut,
    ReportLatestOut,
    ReportOut,
    ReportRecipientOut,
    ReportRegisterIn,
    SnapshotBulkIn,
    SnapshotBulkOut,
    SnapshotDetailOut,
    SnapshotListItemOut,
)
from app.services import generated_report_service
from app.services.analytics_service import compute_snapshot_deltas
from app.services.business_service import (
    create_business_and_competitors,
    get_business_with_competitors,
    list_businesses,
)
from app.services.generated_report_service import (
    get_latest_report_for_business,
    insert_generated_report,
)
from app.services.insight_presentation_service import build_client_facing_insights
from app.services.insights.money_insights import build_money_insights
from app.services.insights_service import (
    add_challenger_gap_insight,
    add_competitive_tier_pressure_insight,
    add_competitor_surge_insight,
    add_leader_pulling_away_insight,
    add_market_concentration_insight,
    add_market_quiet_insight,
    add_weekly_actions_insight,
    suppress_market_quiet_if_owner_centric,
    build_executive_headline,
)
from app.services.momentum_service import MomentumInputs, compute_competitor_momentum
from app.services.recipient_service import (
    list_recipients_for_business,
    upsert_recipients_for_business,
)
from app.services.report_periods import Schedule, compute_next_run_at
from app.services.report_schedule_service import upsert_schedule_for_business
from app.services.report_service import (
    get_latest_report,
    get_report_by_id,
    list_reports_by_business,
    register_report,
)
from app.services.review_batch import ingest_reviews_for_business
from app.services.review_insight_engine import (
    build_review_insights_for_business,
    get_review_rows_for_business,
)
from app.services.review_insight_formatter import format_insights_for_report
from app.services.review_velocity_service import (
    ReviewVelocityInputs,
    compute_review_velocity_trend,
)
from app.services.share_of_voice_service import compute_share_of_voice_from_deltas
from app.services.snapshot_service import (
    collect_snapshots_for_business,
    get_snapshot_by_id,
    insert_snapshots_bulk,
    list_snapshots_by_business,
)
from app.services.threat_detection_service import ThreatInputs, compute_threat
from app.services.review_friction_service import (
    build_review_theme_counts,
    build_customer_friction_insights,
    build_customer_friction_summary,
)

logger = logging.getLogger(__name__)

router = APIRouter()
router.include_router(analytics_router)
router.include_router(report_schedules_router)
router.include_router(generated_reports_router)
router.include_router(review_ingestion_router)


class CreateCheckoutSessionIn(BaseModel):
    business_id: UUID
    plan: str


# --------------------
# Health
# --------------------


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/health/db")
def health_db():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("select 1;")
        return {"db": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"db error: {str(e)}")


# --------------------
# Billing / Stripe
# --------------------


def _get_stripe_price_id_for_plan(plan: str) -> str:
    plan_normalized = (plan or "").strip().lower()

    if plan_normalized == "starter":
        if not settings.stripe_price_starter:
            raise HTTPException(status_code=500, detail="Missing STRIPE_PRICE_STARTER")
        return settings.stripe_price_starter

    if plan_normalized == "growth":
        if not settings.stripe_price_growth:
            raise HTTPException(status_code=500, detail="Missing STRIPE_PRICE_GROWTH")
        return settings.stripe_price_growth

    raise HTTPException(status_code=400, detail="Plan must be 'starter' or 'growth'")


def _stripe_obj_to_dict(obj: Any) -> dict:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "_to_dict_recursive"):
        try:
            return obj._to_dict_recursive()
        except Exception:
            pass
    try:
        return dict(obj)
    except Exception:
        return {}


def _is_billing_active(status: str | None) -> bool:
    return (status or "").strip().lower() in {"active", "trialing"}


def _upsert_business_billing_state(
    *,
    business_id: str,
    stripe_customer_id: str | None = None,
    stripe_subscription_id: str | None = None,
    stripe_price_id: str | None = None,
    billing_status: str | None = None,
    billing_current_period_end: datetime | None = None,
    is_active: bool | None = None,
) -> None:
    fields = []
    values = []

    if stripe_customer_id is not None:
        fields.append("stripe_customer_id = %s")
        values.append(stripe_customer_id)

    if stripe_subscription_id is not None:
        fields.append("stripe_subscription_id = %s")
        values.append(stripe_subscription_id)

    if stripe_price_id is not None:
        fields.append("stripe_price_id = %s")
        values.append(stripe_price_id)

    if billing_status is not None:
        fields.append("billing_status = %s")
        values.append(billing_status)

    if billing_current_period_end is not None:
        fields.append("billing_current_period_end = %s")
        values.append(billing_current_period_end)

    if is_active is not None:
        fields.append("is_active = %s")
        values.append(is_active)

    if not fields:
        return

    values.append(business_id)

    sql = f"""
        update businesses
        set {", ".join(fields)}
        where id = %s
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(values))
        conn.commit()


def _get_business_id_from_subscription_obj(subscription: Any) -> str | None:
    subscription = _stripe_obj_to_dict(subscription)
    metadata = subscription.get("metadata") or {}
    business_id = metadata.get("business_id")
    if business_id:
        return str(business_id)
    return None


def _sync_business_from_checkout_session(session_id: str) -> dict:
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=500, detail="Missing STRIPE_SECRET_KEY")

    stripe.api_key = settings.stripe_secret_key

    checkout_session = _stripe_obj_to_dict(
        stripe.checkout.Session.retrieve(session_id)
    )

    business_id = checkout_session.get("client_reference_id")
    subscription_id = checkout_session.get("subscription")
    customer_id = checkout_session.get("customer")

    if not business_id:
        raise HTTPException(status_code=400, detail="Checkout Session missing client_reference_id")

    if not subscription_id:
        raise HTTPException(status_code=400, detail="Checkout Session missing subscription")

    subscription = _stripe_obj_to_dict(
        stripe.Subscription.retrieve(subscription_id)
    )

    status = subscription.get("status")
    items = (((subscription.get("items") or {}).get("data")) or [])
    price_id = None
    if items:
        price_id = (((items[0] or {}).get("price")) or {}).get("id")

    # Always derive from items (your Stripe version requires this)
    items = (((subscription.get("items") or {}).get("data")) or [])

    current_period_end = None
    if items:
        current_period_end = items[0].get("current_period_end")

    current_period_end_dt = None
    if current_period_end:
        current_period_end_dt = datetime.fromtimestamp(
            int(current_period_end),
            tz=timezone.utc,
        )

    # Fallback: try nested location if top-level missing
    if not current_period_end:
        items = (((subscription.get("items") or {}).get("data")) or [])
        if items:
            current_period_end = items[0].get("current_period_end")

    current_period_end_dt = None
    if current_period_end:
        current_period_end_dt = datetime.fromtimestamp(
            int(current_period_end),
            tz=timezone.utc,
        )

    _upsert_business_billing_state(
        business_id=str(business_id),
        stripe_customer_id=str(customer_id) if customer_id else None,
        stripe_subscription_id=str(subscription_id),
        stripe_price_id=str(price_id) if price_id else None,
        billing_status=str(status) if status else None,
        billing_current_period_end=current_period_end_dt,
        is_active=_is_billing_active(status),
    )

    return {
        "business_id": str(business_id),
        "customer_id": str(customer_id) if customer_id else None,
        "subscription_id": str(subscription_id),
        "price_id": str(price_id) if price_id else None,
        "status": str(status) if status else None,
        "is_active": _is_billing_active(status),
    }


@router.post("/billing/create-checkout-session")
def create_checkout_session(payload: CreateCheckoutSessionIn):
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=500, detail="Missing STRIPE_SECRET_KEY")
    if not settings.stripe_success_url:
        raise HTTPException(status_code=500, detail="Missing STRIPE_SUCCESS_URL")
    if not settings.stripe_cancel_url:
        raise HTTPException(status_code=500, detail="Missing STRIPE_CANCEL_URL")

    stripe.api_key = settings.stripe_secret_key

    business_id = str(payload.business_id)
    plan = (payload.plan or "").strip().lower()
    price_id = _get_stripe_price_id_for_plan(plan)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, name, stripe_customer_id, is_active
                from businesses
                where id = %s
                """,
                (business_id,),
            )
            business = cur.fetchone()

    if not business:
        raise HTTPException(status_code=404, detail="Business not found")

    business_name = business.get("name") or "Pulse LCI Client"
    stripe_customer_id = business.get("stripe_customer_id")

    try:
        session_kwargs = {
            "mode": "subscription",
            "success_url": settings.stripe_success_url,
            "cancel_url": settings.stripe_cancel_url,
            "line_items": [
                {
                    "price": price_id,
                    "quantity": 1,
                }
            ],
            "client_reference_id": business_id,
            "metadata": {
                "business_id": business_id,
                "plan": plan,
            },
            "subscription_data": {
                "metadata": {
                    "business_id": business_id,
                    "plan": plan,
                }
            },
        }

        if stripe_customer_id:
            session_kwargs["customer"] = stripe_customer_id

        session = stripe.checkout.Session.create(**session_kwargs)

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create Stripe Checkout Session: {e}",
        )

    return {
        "ok": True,
        "url": session.url,
        "session_id": session.id,
        "business_id": business_id,
        "plan": plan,
        "price_id": price_id,
        "existing_customer": bool(stripe_customer_id),
        "business_name": business_name,
    }

@router.post("/admin/billing/checkout-link")
def admin_checkout_link(payload: CreateCheckoutSessionIn):
    return create_checkout_session(payload)

@router.post("/billing/webhook")
async def stripe_webhook(request: Request):
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=500, detail="Missing STRIPE_SECRET_KEY")
    if not settings.stripe_webhook_secret:
        raise HTTPException(status_code=500, detail="Missing STRIPE_WEBHOOK_SECRET")

    stripe.api_key = settings.stripe_secret_key

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig_header,
            secret=settings.stripe_webhook_secret,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid webhook payload: {e}")
    except stripe.error.SignatureVerificationError as e:
        raise HTTPException(status_code=400, detail=f"Invalid webhook signature: {e}")

    event_type = event["type"]
    obj = _stripe_obj_to_dict(event["data"]["object"])

    try:
        if event_type == "checkout.session.completed":
            session_id = obj.get("id")

            full_session = {}
            if session_id:
                full_session = _stripe_obj_to_dict(
                    stripe.checkout.Session.retrieve(session_id)
                )

            business_id = full_session.get("client_reference_id") or obj.get("client_reference_id")
            subscription_id = full_session.get("subscription") or obj.get("subscription")
            customer_id = full_session.get("customer") or obj.get("customer")

            if business_id and subscription_id:
                subscription = _stripe_obj_to_dict(
                    stripe.Subscription.retrieve(subscription_id)
                )

                status = subscription.get("status")
                items = (((subscription.get("items") or {}).get("data")) or [])
                price_id = None
                if items:
                    price_id = (((items[0] or {}).get("price")) or {}).get("id")

                # Always derive from items (your Stripe version requires this)
                items = (((subscription.get("items") or {}).get("data")) or [])

                current_period_end = None
                if items:
                    current_period_end = items[0].get("current_period_end")

                current_period_end_dt = None
                if current_period_end:
                    current_period_end_dt = datetime.fromtimestamp(
                        int(current_period_end),
                        tz=timezone.utc,
                    )

                # Fallback: try nested location if top-level missing
                if not current_period_end:
                    items = (((subscription.get("items") or {}).get("data")) or [])
                    if items:
                        current_period_end = items[0].get("current_period_end")

                
                _upsert_business_billing_state(
                    business_id=str(business_id),
                    stripe_customer_id=str(customer_id) if customer_id else None,
                    stripe_subscription_id=str(subscription_id),
                    stripe_price_id=str(price_id) if price_id else None,
                    billing_status=str(status) if status else None,
                    billing_current_period_end=current_period_end_dt,
                    is_active=(
                        False
                        if event_type == "customer.subscription.deleted"
                        else _is_billing_active(status)
                    ),
                )

        elif event_type in {
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.deleted",
        }:
            subscription = _stripe_obj_to_dict(obj)
            business_id = _get_business_id_from_subscription_obj(subscription)

            if business_id:
                status = subscription.get("status")
                customer_id = subscription.get("customer")
                subscription_id = subscription.get("id")

                items = (((subscription.get("items") or {}).get("data")) or [])
                price_id = None
                if items:
                    price_id = (((items[0] or {}).get("price")) or {}).get("id")

                # Always derive from items (your Stripe version requires this)
                items = (((subscription.get("items") or {}).get("data")) or [])

                current_period_end = None
                if items:
                    current_period_end = items[0].get("current_period_end")

                current_period_end_dt = None
                if current_period_end:
                    current_period_end_dt = datetime.fromtimestamp(
                        int(current_period_end),
                        tz=timezone.utc,
                    )

                # Fallback: try nested location if top-level missing
                if not current_period_end:
                    items = (((subscription.get("items") or {}).get("data")) or [])
                    if items:
                        current_period_end = items[0].get("current_period_end")

                current_period_end_dt = None
                if current_period_end:
                    current_period_end_dt = datetime.fromtimestamp(
                        int(current_period_end),
                        tz=timezone.utc,
                    )

                _upsert_business_billing_state(
                    business_id=str(business_id),
                    stripe_customer_id=str(customer_id) if customer_id else None,
                    stripe_subscription_id=str(subscription_id) if subscription_id else None,
                    stripe_price_id=str(price_id) if price_id else None,
                    billing_status=str(status) if status else None,
                    billing_current_period_end=current_period_end_dt,
                    is_active=_is_billing_active(status),
                )

        elif event_type == "invoice.payment_failed":
            subscription_id = obj.get("subscription")
            customer_id = obj.get("customer")

            if subscription_id:
                subscription = _stripe_obj_to_dict(
                    stripe.Subscription.retrieve(subscription_id)
                )
                business_id = _get_business_id_from_subscription_obj(subscription)

                if business_id:
                    status = subscription.get("status")

                    items = (((subscription.get("items") or {}).get("data")) or [])
                    price_id = None
                    if items:
                        price_id = (((items[0] or {}).get("price")) or {}).get("id")

                    # Always derive from items (your Stripe version requires this)
                    items = (((subscription.get("items") or {}).get("data")) or [])

                    current_period_end = None
                    if items:
                        current_period_end = items[0].get("current_period_end")

                    current_period_end_dt = None
                    if current_period_end:
                        current_period_end_dt = datetime.fromtimestamp(
                            int(current_period_end),
                            tz=timezone.utc,
                        )

                    # Fallback: try nested location if top-level missing
                    if not current_period_end:
                        items = (((subscription.get("items") or {}).get("data")) or [])
                        if items:
                            current_period_end = items[0].get("current_period_end")

                    current_period_end_dt = None
                    if current_period_end:
                        current_period_end_dt = datetime.fromtimestamp(
                            int(current_period_end),
                            tz=timezone.utc,
                        )

                    _upsert_business_billing_state(
                        business_id=str(business_id),
                        stripe_customer_id=str(customer_id) if customer_id else None,
                        stripe_subscription_id=str(subscription_id),
                        stripe_price_id=str(price_id) if price_id else None,
                        billing_status=str(status) if status else "past_due",
                        billing_current_period_end=current_period_end_dt,
                        is_active=False,
                    )

    except Exception as e:
        logger.exception("Stripe webhook handler failed")
        raise HTTPException(status_code=500, detail=f"Webhook processing failed: {e}")

    return {"received": True, "type": event_type}


@router.get("/admin/billing/success", response_class=HTMLResponse)
def billing_success(session_id: str | None = None):
    sync_result = None
    error_text = None

    if session_id:
        try:
            sync_result = _sync_business_from_checkout_session(session_id)
        except Exception as e:
            error_text = str(e)

    return f"""
    <html>
      <body style="font-family: Arial, sans-serif; padding: 40px;">
        <h2>Payment successful</h2>
        <p>Stripe checkout completed.</p>
        <p>Session ID: {session_id or "n/a"}</p>
        <p>Business sync: {"ok" if sync_result else "failed"}</p>
        <pre>{sync_result if sync_result else error_text or ""}</pre>
        <p>You can close this window.</p>
      </body>
    </html>
    """


@router.get("/admin/billing/cancel", response_class=HTMLResponse)
def billing_cancel():
    return """
    <html>
      <body style="font-family: Arial, sans-serif; padding: 40px;">
        <h2>Checkout canceled</h2>
        <p>No payment was completed.</p>
        <p>You can close this window and try again.</p>
      </body>
    </html>
    """


# --------------------
# Business
# --------------------


@router.post("/intake", response_model=BusinessWithCompetitorsOut)
def intake(payload: BusinessIntakeIn):
    try:
        return create_business_and_competitors(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed intake: {str(e)}")


@router.post("/onboarding", response_model=OnboardingOut)
def onboarding(payload: OnboardingIn):
    try:
        intake_payload = BusinessIntakeIn(
            business_name=payload.business.business_name,
            primary_domain=payload.business.primary_domain,
            city=payload.business.city,
            state=payload.business.state,
            country=payload.business.country,
            notes=payload.business.notes,
            competitors=[
                CompetitorIn(
                    name=payload.business.business_name,
                    website_url=payload.business.website_url,
                    google_place_id=payload.business.google_place_id,
                    google_maps_url=payload.business.google_maps_url,
                    is_business=True,
                ),
                *payload.competitors,
            ],
        )

        created = create_business_and_competitors(intake_payload)
        business_id = created.business.id

        recipient_rows = upsert_recipients_for_business(
            business_id,
            [str(email).strip().lower() for email in payload.recipient_emails if str(email).strip()],
        )
        recipients = [ReportRecipientOut(**row) for row in recipient_rows]

        sched = Schedule(
            frequency="monthly",
            day_of_week=None,
            day_of_month=1,
            hour=payload.schedule_hour,
            minute=payload.schedule_minute,
            timezone=payload.schedule_timezone,
        )

        next_run_at = compute_next_run_at(sched)

        schedule_row = upsert_schedule_for_business(
            business_id,
            frequency="monthly",
            day_of_week=None,
            day_of_month=1,
            hour=payload.schedule_hour,
            minute=payload.schedule_minute,
            timezone=payload.schedule_timezone,
            is_enabled=True,
            next_run_at=next_run_at,
        )

        if payload.run_initial_collection:
            ingest_reviews_for_business(str(business_id))
            collect_snapshots_for_business(business_id)

        first_report = None

        if payload.auto_generate_first_report:
            sections = build_full_report_pipeline(business_id)

            report = insert_generated_report(
                business_id=business_id,
                schedule_id=schedule_id,
                period_start=datetime.now(timezone.utc) - timedelta(days=30),
                period_end=datetime.now(timezone.utc),
                status="generated",
                title=f"Competitive Report - {datetime.now().strftime('%Y-%m-%d')}",
                summary_text=(sections.get("report_experience") or {}).get("summary_text") or "Report generated.",
                sections=sections,
                inputs={
                    "source": "onboarding_full_pipeline",
                },
                error=None,
            )

            if hasattr(report, "model_dump"):
                first_report = report.model_dump()
            elif hasattr(report, "dict"):
                first_report = report.dict()
            else:
                first_report = report

            if payload.send_first_report and first_report:
                report_id = first_report.get("id") if isinstance(first_report, dict) else None

                if report_id:
                    for recipient in recipients:
                        send_generated_report_email(
                            UUID(str(report_id)),
                            SendReportRequest(to_email=recipient.email),
                        )

        
        checkout = None
        billing_mode = (payload.billing_mode or "").strip().lower()

        if billing_mode not in {"free_preview", "paid_now"}:
            raise HTTPException(
                status_code=400,
                detail="billing_mode must be 'free_preview' or 'paid_now'",
            )

        if billing_mode == "paid_now":
            checkout = create_checkout_session(
                CreateCheckoutSessionIn(
                    business_id=business_id,
                    plan="starter",
                )
            )
        # Optionally email checkout link
        if (
            billing_mode == "paid_now"
            and payload.send_checkout_email
            and checkout
        ):
            checkout_url = checkout.get("url")

            if checkout_url:
                for recipient in recipients:
                    send_simple_email(
                        to_email=recipient.email,
                        subject="Activate your Pulse LCI account",
                        body=f"""
        Hi,

        Your Pulse Local Competitor Intelligence account is ready.

        Activate your subscription here:
        {checkout_url}

        Once completed, your monthly reports will begin automatically.

        Thanks,
        Pulse LCI
        """.strip(),
                    )
        return OnboardingOut(
            business=created.business,
            competitors=created.competitors,
            recipients=recipients,
            schedule=schedule_row,
            first_report=first_report,
            checkout_url=checkout.get("url") if checkout else None,
            checkout_session_id=checkout.get("session_id") if checkout else None,
            checkout_plan=checkout.get("plan") if checkout else None,
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed onboarding: {repr(e)}")


@router.get("/admin/businesses")
def list_admin_businesses():
    sql = """
    select
        b.id,
        b.name,
        b.primary_domain,
        b.city,
        b.state,
        b.country,
        b.notes,

        c.id as competitor_id,
        c.name as competitor_name,
        c.website_url as competitor_website_url,
        c.google_place_id as competitor_google_place_id,
        c.google_maps_url as competitor_google_maps_url,
        c.is_business as competitor_is_business,

        r.email as recipient_email
    from businesses b
    left join competitors c
        on c.business_id = b.id
    left join report_recipients r
        on r.business_id = b.id
        and coalesce(r.is_enabled, true) = true
    order by b.name asc, c.is_business desc, c.name asc, r.email asc
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()

    businesses = {}

    for row in rows:
        business_id = str(row["id"])

        if business_id not in businesses:
            businesses[business_id] = {
                "id": business_id,
                "business_name": row["name"],
                "primary_domain": row["primary_domain"],
                "website_url": None,
                "city": row["city"],
                "state": row["state"],
                "country": row["country"] or "US",
                "google_place_id": None,
                "google_maps_url": None,
                "notes": row["notes"],
                "competitors": [],
                "recipient_emails": [],
            }

        if row["competitor_id"]:
            comp = {
                "id": str(row["competitor_id"]),
                "name": row["competitor_name"],
                "website_url": row["competitor_website_url"],
                "google_place_id": row["competitor_google_place_id"],
                "google_maps_url": row["competitor_google_maps_url"],
                "is_business": bool(row["competitor_is_business"]),
            }

            existing_comp_ids = {
                c["id"] for c in businesses[business_id]["competitors"]
            }

            if comp["id"] not in existing_comp_ids:
                if comp["is_business"]:
                    businesses[business_id]["google_place_id"] = comp["google_place_id"]
                    businesses[business_id]["google_maps_url"] = comp["google_maps_url"]
                    businesses[business_id]["website_url"] = comp["website_url"]
                else:
                    businesses[business_id]["competitors"].append(comp)

        if row["recipient_email"]:
            if row["recipient_email"] not in businesses[business_id]["recipient_emails"]:
                businesses[business_id]["recipient_emails"].append(row["recipient_email"])

    return list(businesses.values())

@router.get("/business/{business_id}", response_model=BusinessWithCompetitorsOut)
def get_business(business_id: UUID):
    result = get_business_with_competitors(business_id)
    if not result:
        raise HTTPException(status_code=404, detail="Business not found")
    return result


@router.get("/businesses", response_model=List[BusinessOut])
def get_businesses():
    return list_businesses()

def _build_share_of_voice_donut_payload(sections: dict) -> dict | None:
    import base64
    import io
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    share = (sections or {}).get("share_of_voice") or {}
    rows = share.get("rows") or []

    if not isinstance(rows, list) or not rows:
        return None

    valid_rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        competitor_name = str(row.get("competitor_name") or "").strip()
        if not competitor_name:
            continue

        # --- SAFE reviews_total (never drop owner) ---
        is_business = bool(row.get("is_business"))

        try:
            rt_raw = row.get("reviews_total")
            rt_num = float(rt_raw if rt_raw is not None else 0)

            if math.isnan(rt_num) or math.isinf(rt_num):
                if is_business:
                    reviews_total = 0
                else:
                    continue
            else:
                reviews_total = int(rt_num)

        except (TypeError, ValueError):
            if is_business:
                reviews_total = 0
            else:
                continue

        # --- SAFE share_pct ---
        try:
            sp_raw = row.get("share_pct")
            sp_num = float(sp_raw if sp_raw is not None else 0)
            if math.isnan(sp_num) or math.isinf(sp_num):
                sp_num = 0.0
            share_pct = sp_num
        except (TypeError, ValueError):
            share_pct = 0.0


        valid_rows.append(
            {
                "competitor_name": competitor_name,
                "reviews_total": reviews_total,
                "share_pct": share_pct,
                "is_business": is_business,
            }
        )

    if not valid_rows:
        return None

    valid_rows.sort(key=lambda x: x["share_pct"], reverse=True)

    owner_color = "#1f4e79"
    competitor_palette = [
        "#a9bacb",  # light steel blue
        "#4f9a9a",  # teal
        "#8a92b2",  # slate violet
        "#c7d1db",  # soft fallback
    ]

    values = []
    colors = []
    legend_items = []
    owner_row = next((r for r in valid_rows if r["is_business"]), None)

    color_index = 0
    for row in valid_rows:
        if row["is_business"]:
            color = owner_color
            label = f"YOU — {row['competitor_name']}"
        else:
            color = competitor_palette[color_index % len(competitor_palette)]
            color_index += 1
            label = row["competitor_name"]

        percent_display = round(row["share_pct"])
        if percent_display == 0 and row["share_pct"] > 0:
            percent_display = 1

        values.append(row["share_pct"])
        colors.append(color)

        legend_items.append(
            {
                "label": label,
                "reviews_total": row["reviews_total"],
                "percent_display": percent_display,
                "is_business": row["is_business"],
                "color": color,
            }
        )

    total_share = sum(v for v in values if isinstance(v, (int, float)))
    if total_share <= 0:
        return None

    owner_percent_display = round(owner_row["share_pct"]) if owner_row else 0

    fig, ax = plt.subplots(figsize=(2.4, 2.4), dpi=160)

    # --- base donut ---
    explode = [
        0.03 if row["is_business"] else 0
        for row in valid_rows
    ]

    wedges, _ = ax.pie(
        values,
        colors=colors,
        startangle=90,
        counterclock=False,
        explode=explode,  # 👈 subtle outer pop
        wedgeprops={
            "width": 0.35,
            "edgecolor": "white",
            "linewidth": 2.0,
        },
    )

    # --- thicker YOU wedge overlay (inner pop) ---
    total_value = sum(values)

    for i, row in enumerate(valid_rows):
        if row["is_business"]:
            start_angle = 90 - ((sum(values[:i]) / total_value) * 360.0)

            ax.pie(
                [values[i], total_value - values[i]],
                colors=[colors[i], "none"],
                startangle=start_angle,
                counterclock=False,
                explode=[0.03, 0],  # 👈 match base explode
                wedgeprops={
                    "width": 0.45,  # 👈 inner + outer pop
                    "edgecolor": "white",
                    "linewidth": 2.0,
                },
            )
            break

    ax.text(
        0,
        -0.05,
        f"{owner_percent_display}%",
        ha="center",
        fontsize=20,
        fontweight="bold",
        color=owner_color,
    )
    ax.text(
        0,
        -0.22,
        "of reviews",
        ha="center",
        fontsize=8,
        color="#5b6570",
    )

    ax.set_aspect("equal")
    ax.axis("off")

    buf = io.BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)

    buf.seek(0)
    chart_base64 = base64.b64encode(buf.read()).decode("utf-8")

    return {
        "owner_percent_display": owner_percent_display,
        "legend_items": legend_items,
        "chart_base64": chart_base64,
    }

# --------------------
# Snapshots
# --------------------

def _build_review_count_bar_payload(sections: dict) -> dict | None:
    share = (sections or {}).get("share_of_voice") or {}
    rows = share.get("rows") or []

    if not isinstance(rows, list) or not rows:
        return None

    valid_rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        competitor_name = str(row.get("competitor_name") or row.get("name") or "").strip()
        if not competitor_name:
            continue

        # --- SAFE reviews_total ---
        try:
            rt_raw = row.get("reviews_total")
            rt_num = float(rt_raw if rt_raw is not None else 0)
            if math.isnan(rt_num) or math.isinf(rt_num):
                continue
            reviews_total = int(rt_num)
        except (TypeError, ValueError):
            continue

        is_business = bool(row.get("is_business"))

        valid_rows.append(
            {
                "competitor_name": competitor_name,
                "reviews_total": reviews_total,
                "is_business": is_business,
            }
        )
    if not valid_rows:
        return None

    valid_rows.sort(key=lambda x: x["reviews_total"], reverse=True)

    owner_color = "#1f4e79"
    competitor_palette = ["#a9bacb", "#4f9a9a", "#8a92b2", "#c7d1db", "#7f8ea3", "#b8c6d3"]

    labels = []
    values = []
    colors = []

    color_index = 0
    for row in valid_rows:
        labels.append(row["competitor_name"])
        values.append(row["reviews_total"])

        if row["is_business"]:
            colors.append(owner_color)
        else:
            colors.append(competitor_palette[color_index % len(competitor_palette)])
            color_index += 1

    fig, ax = plt.subplots(figsize=(6.2, 2.8), dpi=160)
    y_positions = list(range(len(labels)))

    bars = ax.barh(y_positions, values, color=colors, height=0.58)

    ax.set_yticks(y_positions)
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()

    ax.set_xlabel("Google reviews", fontsize=9, color="#5b6570")
    ax.tick_params(axis="x", labelsize=8, colors="#5b6570")
    ax.tick_params(axis="y", labelsize=9, colors="#243248")

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.spines["bottom"].set_color("#d9dee5")
    ax.grid(axis="x", color="#edf1f5", linewidth=0.8)
    ax.set_axisbelow(True)

    max_value = max(values) if values else 0
    ax.set_xlim(0, max_value * 1.18 if max_value else 1)

    for i, (bar, row) in enumerate(zip(bars, valid_rows)):
        value = row["reviews_total"]
        x = bar.get_width()
        y = bar.get_y() + (bar.get_height() / 2)

        label_color = owner_color if row["is_business"] else "#243248"
        font_weight = "bold" if row["is_business"] else "normal"

        ax.text(
            x + (max_value * 0.015 if max_value else 0.5),
            y,
            f"{value}",
            va="center",
            ha="left",
            fontsize=9,
            color=label_color,
            fontweight=font_weight,
        )

    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    buf = io.BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)

    buf.seek(0)
    chart_base64 = base64.b64encode(buf.read()).decode("utf-8")

    owner_row = next((r for r in valid_rows if r["is_business"]), None)
    leader_row = valid_rows[0] if valid_rows else None
    gap_to_leader = None
    if owner_row and leader_row and owner_row["competitor_name"] != leader_row["competitor_name"]:
        gap_to_leader = max(0, leader_row["reviews_total"] - owner_row["reviews_total"])

    return {
        "chart_base64": chart_base64,
        "leader_name": leader_row["competitor_name"] if leader_row else None,
        "leader_reviews": leader_row["reviews_total"] if leader_row else None,
        "owner_reviews": owner_row["reviews_total"] if owner_row else None,
        "gap_to_leader": gap_to_leader,
    }

def _build_review_pulse_payload(business_id: UUID, days: int = 30) -> dict | None:
    import base64
    import io
    import math
    from datetime import datetime, timedelta, timezone

    def _safe_int(value):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if math.isnan(number) or math.isinf(number):
            return None
        return int(number)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    owner_color = "#1f4e79"
    competitor_palette = ["#a9bacb", "#4f9a9a", "#8a92b2", "#c7d1db", "#7f8ea3", "#b8c6d3"]

    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=days)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                with daily_latest as (
                    select
                        s.competitor_id,
                        c.name as competitor_name,
                        c.is_business,
                        date(s.observed_at) as snap_day,
                        s.google_review_count,
                        row_number() over (
                            partition by s.competitor_id, date(s.observed_at)
                            order by s.observed_at desc, s.id desc
                        ) as rn
                    from snapshots s
                    join competitors c on c.id = s.competitor_id
                    where s.business_id = %s
                      and s.observed_at >= %s
                      and s.google_review_count is not null
                )
                select
                    competitor_id,
                    competitor_name,
                    is_business,
                    snap_day,
                    google_review_count
                from daily_latest
                where rn = 1
                order by competitor_name, snap_day
                """,
                (str(business_id), start_dt),
            )
            rows = cur.fetchall()

            cur.execute(
                """
                with baseline as (
                    select
                        s.competitor_id,
                        s.google_review_count,
                        row_number() over (
                            partition by s.competitor_id
                            order by s.observed_at desc, s.id desc
                        ) as rn
                    from snapshots s
                    where s.business_id = %s
                      and s.observed_at < %s
                      and s.google_review_count is not null
                )
                select competitor_id, google_review_count
                from baseline
                where rn = 1
                """,
                (str(business_id), start_dt),
            )
            baseline_rows = cur.fetchall()

    if not rows:
        return {"debug": "no_rows"}

    baseline_by_competitor = {}
    for r in baseline_rows:
        try:
            safe_count = _safe_int(r["google_review_count"])
            if safe_count is None:
                continue
            safe_count = _safe_int(r["google_review_count"])
            if safe_count is None:
                continue
            baseline_by_competitor[str(r["competitor_id"])] = safe_count
        except (TypeError, ValueError):
            continue

    series_by_competitor: dict[str, dict] = {}
    all_days = [start_dt.date() + timedelta(days=i) for i in range(days + 1)]

    for r in rows:
        comp_id = str(r["competitor_id"])
        try:
            review_count = _safe_int(r["google_review_count"])
            if review_count is None:
                continue
            if review_count is None:
                continue
        except (TypeError, ValueError):
            continue

        if comp_id not in series_by_competitor:
            series_by_competitor[comp_id] = {
                "name": r["competitor_name"],
                "is_business": bool(r["is_business"]),
                "daily_counts": {},
            }

        series_by_competitor[comp_id]["daily_counts"][r["snap_day"]] = review_count

    print("review_pulse series_by_competitor:", len(series_by_competitor))
    if not series_by_competitor:
        return None

    fig, ax = plt.subplots(figsize=(6.2, 2.9), dpi=160)

    color_index = 0
    plotted_any = False

    for comp_id, payload in sorted(
        series_by_competitor.items(),
        key=lambda item: (not item[1]["is_business"], item[1]["name"])
    ):
        if payload["is_business"]:
            color = owner_color
            linewidth = 2.8
            alpha = 1.0
        else:
            color = competitor_palette[color_index % len(competitor_palette)]
            color_index += 1
            linewidth = 1.8
            alpha = 0.95

        daily_counts = payload["daily_counts"]
        if not daily_counts:
            continue

        baseline_count = baseline_by_competitor.get(comp_id)
        if baseline_count is None:
            first_day_with_data = min(daily_counts.keys())
            baseline_count = daily_counts[first_day_with_data]

        if baseline_count is None:
            continue

        x_values = []
        y_values = []
        running_count = baseline_count

        for day in all_days:
            if day in daily_counts:
                running_count = daily_counts[day]

            if running_count is None:
                continue

            try:
                delta = float(running_count) - float(baseline_count)
            except (TypeError, ValueError):
                continue

            if math.isnan(delta) or math.isinf(delta):
                continue

            delta = int(delta)

            x_values.append(day)
            y_values.append(delta)

        if not x_values or not y_values:
            continue

        ax.plot(
            x_values,
            y_values,
            color=color,
            linewidth=linewidth,
            alpha=alpha,
            label=payload["name"],
        )
        plotted_any = True

    print("review_pulse plotted_any:", plotted_any)
    if not plotted_any:
        plt.close(fig)
        return None

    ax.axhline(0, color="#d9dee5", linewidth=1.0)

    ax.set_title("Review Pulse (last 30 days)", fontsize=10, color="#243248", pad=10)
    ax.set_ylabel("Net review change", fontsize=9, color="#5b6570")
    ax.tick_params(axis="x", labelsize=8, colors="#5b6570")
    ax.tick_params(axis="y", labelsize=8, colors="#5b6570")

    locator = mdates.WeekdayLocator(interval=1)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#d9dee5")
    ax.spines["bottom"].set_color("#d9dee5")
    ax.grid(axis="y", color="#edf1f5", linewidth=0.8)
    ax.set_axisbelow(True)

    fig.autofmt_xdate(rotation=0)
    ax.legend(
        loc="upper left",
        bbox_to_anchor=(1.01, 1.02),
        frameon=False,
        fontsize=8,
    )

    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    buf = io.BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)

    buf.seek(0)
    chart_base64 = base64.b64encode(buf.read()).decode("utf-8")

    return {
        "chart_base64": chart_base64,
    }


def _safe_int(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return int(number)

@router.post("/snapshot/bulk", response_model=SnapshotBulkOut)
def snapshot_bulk(payload: SnapshotBulkIn):
    try:
        return insert_snapshots_bulk(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to insert snapshots: {str(e)}")


@router.get("/snapshots", response_model=List[SnapshotListItemOut])
def get_snapshots(business_id: UUID):
    return list_snapshots_by_business(business_id)


@router.get("/snapshot/{snapshot_id}", response_model=SnapshotDetailOut)
def get_snapshot(snapshot_id: UUID):
    snap = get_snapshot_by_id(snapshot_id)
    if not snap:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return snap


@router.delete("/snapshot/{snapshot_id}")
def delete_snapshot(snapshot_id: UUID):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM snapshots WHERE id = %s RETURNING id", (snapshot_id,))
                row = cur.fetchone()

            if row is None:
                raise HTTPException(status_code=404, detail="Snapshot not found")

            conn.commit()

        return {"deleted_snapshot_id": str(snapshot_id)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete snapshot: {str(e)}")


# --------------------
# Reports (legacy “reports” table)
# --------------------


@router.post("/report/register", response_model=ReportOut)
def report_register(payload: ReportRegisterIn):
    try:
        return register_report(payload)
    except Exception as e:
        msg = str(e)
        if "uq_reports_business_period" in msg or "duplicate key value" in msg:
            raise HTTPException(
                status_code=409,
                detail="Report for that business + period already exists",
            )
        raise HTTPException(status_code=500, detail=f"Failed to register report: {msg}")


@router.get("/reports/{business_id}/latest", response_model=ReportLatestOut)
def reports_latest(business_id: UUID):
    try:
        report = get_latest_report_for_business(business_id)
        return {"report": report, "signed_url_placeholder": None}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch latest report: {str(e)}")


def _normalize_sections(sections: Any) -> Any:
    if not isinstance(sections, dict):
        return sections

    sov = sections.get("share_of_voice")

    if isinstance(sov, dict) or sov is None:
        pass
    elif isinstance(sov, str):
        s = sov.strip()
        if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
            try:
                sections["share_of_voice"] = json.loads(s)
            except Exception:
                sections["share_of_voice"] = {"market_total_reviews": 0, "rows": []}
        else:
            sections["share_of_voice"] = {"market_total_reviews": 0, "rows": []}
    else:
        sections["share_of_voice"] = {"market_total_reviews": 0, "rows": []}

    sections["share_of_voice_donut"] = _build_share_of_voice_donut_payload(sections)
    sections["review_count_bar"] = _build_review_count_bar_payload(sections)
    return sections

def _build_report_experience_payload(
    insights: Any,
    previous_insights: Any = None,
    sections: Any = None,
) -> Dict[str, Any]:
    safe_insights = insights if isinstance(insights, list) else []
    safe_previous_insights = previous_insights if isinstance(previous_insights, list) else []
    safe_sections = sections if isinstance(sections, dict) else {}

    presentation = build_client_facing_insights(
        safe_insights,
        previous_insights=safe_previous_insights,
        sections=safe_sections,
    )

    def _normalize_item(item: Dict[str, Any]) -> Dict[str, Any]:
        summary = (
            item.get("summary")
            or item.get("message")
            or item.get("implication")
            or item.get("recommended_action")
            or ""
        )

        action = item.get("action") or item.get("recommended_action") or summary

        why = (
            item.get("why_it_matters")
            or item.get("implication")
            or "This signal may affect local visibility, trust, or customer choice."
        )

        how = item.get("how_to_implement") or (
            "Review this signal, assign an owner, and take one clear action before the next report."
        )

        priority_map = {
            "high": "Immediate",
            "critical": "Immediate",
            "medium": "Next",
            "low": "Monitor",
            "immediate": "Immediate",
            "next": "Next",
            "monitor": "Monitor",
        }

        raw_priority = str(item.get("priority") or "").strip().lower()
        mapped_priority = priority_map.get(raw_priority, "Next")

        return {
            **item,
            "summary": summary,
            "action": action,
            "why_it_matters": why,
            "how_to_implement": how,
            "priority": mapped_priority,
            "section": item.get("section") or "Strategic Recommendations",
            "severity": item.get("severity") or "info",
            "title": item.get("title") or summary,
            "detail": item.get("detail") or how,
        }

    normalized_insights = [
        _normalize_item(i)
        for i in safe_insights
        if isinstance(i, dict)
    ]

    flat_insights = normalized_insights[:10]

    grouped_sections = presentation.get("grouped_sections") or []

    fixed_groups = []
    for group in grouped_sections:
        if not isinstance(group, dict):
            continue

        group_items = group.get("insights") or group.get("items") or []
        group_items = [
            _normalize_item(i)
            for i in group_items
            if isinstance(i, dict)
        ]

        if group_items:
            fixed_groups.append(
                {
                    "section": group.get("section") or group.get("title") or "Strategic Recommendations",
                    "insights": group_items,
                }
            )

    grouped_sections = fixed_groups

    if not flat_insights:
        flat_insights = normalized_insights[:8]

    if not grouped_sections:
        grouped_sections = [
            {
                "section": "Strategic Recommendations",
                "insights": normalized_insights[:6],
            }
        ]

    this_month_focus = [
        _normalize_item(i)
        for i in (presentation.get("this_month_focus") or [])
        if isinstance(i, dict)
    ]

    if not this_month_focus:
        immediate_items = [
            i for i in normalized_insights
            if i.get("priority") == "Immediate"
        ]
        this_month_focus = (immediate_items or normalized_insights)[:3]

    action_plan = presentation.get("action_plan") or {}
    if not isinstance(action_plan, dict):
        action_plan = {}

    action_plan["immediate"] = [
        _normalize_item(i)
        for i in (action_plan.get("immediate") or this_month_focus[:3])
        if isinstance(i, dict)
    ]

    action_plan["next"] = [
        _normalize_item(i)
        for i in (action_plan.get("next") or normalized_insights[3:6] or normalized_insights[:3])
        if isinstance(i, dict)
    ]

    action_plan["monitor"] = [
        _normalize_item(i)
        for i in (action_plan.get("monitor") or normalized_insights[6:8])
        if isinstance(i, dict)
    ]

    return {
        "flat_insights": flat_insights,
        "grouped_sections": grouped_sections,
        "summary_text": presentation.get("summary_text") or "",
        "action_plan": action_plan,
        "this_month_focus": this_month_focus,
        "review_target": presentation.get("review_target"),
        "review_pace": presentation.get("review_pace"),
        "review_overtake": presentation.get("review_overtake"),
    }

def _build_review_buckets_from_business_reviews(
    business_id: UUID,
) -> tuple[list[dict], list[dict]]:
    owner_reviews: list[dict] = []
    competitor_reviews: list[dict] = []

    sql = """
        select
            c.is_business,
            r.rating,
            r.text
        from reviews r
        join competitors c
          on c.id = r.competitor_id
        where c.business_id = %s
          and r.text is not null
    """

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (str(business_id),))
                rows = cur.fetchall() or []
    except Exception as e:
        logger.warning("review bucket query failed: %s", e)
        return owner_reviews, competitor_reviews

    for row in rows:
        is_owner = False
        rating = None
        text = None

        if isinstance(row, dict):
            is_owner = bool(row.get("is_business"))
            rating = row.get("rating")
            text = row.get("text")
        else:
            try:
                is_owner = bool(row[0])
                rating = row[1]
                text = row[2]
            except Exception:
                continue

        if not text:
            continue

        item = {
            "rating": rating,
            "text": text,
        }

        if is_owner:
            owner_reviews.append(item)
        else:
            competitor_reviews.append(item)

    return owner_reviews, competitor_reviews


@router.get("/reports/{business_id}", response_model=list[GeneratedReportOut])
def list_reports_for_business(business_id: UUID):
    rows = generated_report_service.list_reports_for_business(business_id)

    fixed: List[Any] = []
    for r in rows:
        if isinstance(r, dict):
            if "sections" in r:
                r["sections"] = _normalize_sections(r["sections"])
            fixed.append(r)
        else:
            try:
                r.sections = _normalize_sections(getattr(r, "sections", None))
            except Exception:
                pass
            fixed.append(r)

    return fixed


@router.get("/reports", response_model=List[ReportOut])
def reports_list(business_id: UUID):
    try:
        return list_reports_by_business(business_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch reports: {str(e)}")


@router.get("/report/{report_id}", response_model=GeneratedReportOut)
def report_detail(report_id: UUID):
    report = generated_report_service.get_report_by_id(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


# --------------------
# Step 5/6 helpers (generated_reports table)
# --------------------


def _as_dict(x: Any) -> Dict[str, Any]:
    if isinstance(x, dict):
        return x
    if hasattr(x, "model_dump"):
        try:
            return x.model_dump()
        except Exception:
            return {}
    if hasattr(x, "dict"):
        try:
            return x.dict()
        except Exception:
            return {}
    return {}


def _fetch_latest_report_sections(business_id: UUID) -> Tuple[Optional[Dict[str, Any]], Optional[UUID]]:
    """
    Returns (latest_sections, latest_report_id) for this business.

    IMPORTANT: your generated_reports table does NOT have created_at,
    so we order by period_end, then period_start, then id (tie-breaker).
    """
    sql = """
        SELECT id, sections
        FROM generated_reports
        WHERE business_id = %s
        ORDER BY period_end DESC, period_start DESC, id DESC
        LIMIT 1
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(business_id),))
            row = cur.fetchone()

    if not row:
        return None, None

    if isinstance(row, dict):
        rid = row.get("id")
        sections = row.get("sections")
    else:
        rid = row[0]
        sections = row[1]

    sections = sections or {}
    if not isinstance(sections, dict):
        sections = _normalize_sections(sections)

    try:
        rid_uuid = UUID(str(rid))
    except Exception:
        rid_uuid = None

    return sections, rid_uuid


def _append_insight_to_report_in_db(
    report_id: UUID,
    insight: dict,
    previous_insights: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """
    Reads generated_reports.sections, appends insight into sections.insights,
    rebuilds sections.report_experience, then writes back.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT sections FROM generated_reports WHERE id = %s", (str(report_id),))
            row = cur.fetchone()
            if not row:
                return

            sections = row.get("sections") if isinstance(row, dict) else row[0]
            sections = sections or {}
            if not isinstance(sections, dict):
                sections = _normalize_sections(sections)

            insights = sections.setdefault("insights", [])
            if not isinstance(insights, list):
                insights = []
                sections["insights"] = insights

            insights.append(insight)
            sections["report_experience"] = _build_report_experience_payload(
                insights,
                previous_insights=previous_insights,
                sections=sections,
            )
            sections["share_of_voice_donut"] = _build_share_of_voice_donut_payload(sections)
            sections["review_count_bar"] = _build_review_count_bar_payload(sections)
            # Keep existing review pulse chart if present.
            # Do not wipe it during Step 5/6 post-processing.
            sections["review_pulse"] = sections.get("review_pulse")

            cur.execute(
                "UPDATE generated_reports SET sections = %s WHERE id = %s",
                (json.dumps(sections), str(report_id)),
            )
        conn.commit()


# --------------------
# Generated Reports
# --------------------


@router.post("/business/{business_id}/reports/generate", response_model=GeneratedReportOut)
def generate_business_report(business_id: UUID):
    """
    Manual report generation:
      - inserts generated_reports row with summary_text + sections
      - sections: top_moves, insights, momentum, velocity_trends, threats, share_of_voice
      - Step 5/6 compares PREVIOUS latest report (pre-insert) vs this new report (post-insert)
    """
    try:
        days = 30

        prev_sections, prev_report_id = _fetch_latest_report_sections(business_id)

        now = datetime.now(timezone.utc)
        period_end = now
        period_start = now - timedelta(days=days)
        title = f"Competitive Report ({days}d) - {now.strftime('%Y-%m-%d')}"

        schedule_id = UUID("00000000-0000-0000-0000-000000000000")
        schedule_meta = None

        try:
            from app.services.report_schedule_service import get_schedule_for_business

            schedule_meta = get_schedule_for_business(business_id)
        except Exception:
            schedule_meta = None

        if isinstance(schedule_meta, dict) and schedule_meta.get("id"):
            try:
                schedule_id = UUID(str(schedule_meta["id"]))
            except Exception:
                schedule_id = UUID("00000000-0000-0000-0000-000000000000")

        raw = compute_snapshot_deltas(business_id=business_id, days=days)

        as_of = None
        competitor_deltas: List[Dict[str, Any]] = []

        if isinstance(raw, list):
            competitor_deltas = raw
        elif isinstance(raw, dict):
            as_of = raw.get("as_of")
            competitor_deltas = raw.get("deltas") or raw.get("competitors") or []
        else:
            competitor_deltas = []

        competitor_deltas = [d for d in competitor_deltas if isinstance(d, dict)]

        # ---------- FALLBACK DELTAS ----------
        if not competitor_deltas:
            snaps_any = list_snapshots_by_business(business_id)

            def _to_dict(x: Any) -> Optional[Dict[str, Any]]:
                if isinstance(x, dict):
                    return x
                if hasattr(x, "model_dump"):
                    try:
                        return x.model_dump()
                    except Exception:
                        return None
                if hasattr(x, "dict"):
                    try:
                        return x.dict()
                    except Exception:
                        return None
                return None

            snaps: List[Dict[str, Any]] = []
            if isinstance(snaps_any, list):
                for s in snaps_any:
                    d = _to_dict(s)
                    if d:
                        snaps.append(d)

            by: Dict[str, List[Dict[str, Any]]] = {}
            for s in snaps:
                cid = s.get("competitor_id") or s.get("competitor_name") or ""
                cid = str(cid)
                if not cid:
                    continue
                by.setdefault(cid, []).append(s)

            tmp: List[Dict[str, Any]] = []
            for _, items in by.items():
                items = [x for x in items if x.get("observed_at")]
                if len(items) < 2:
                    continue

                items = [x for x in items if x.get("observed_at")]
                if not items:
                    continue

                items.sort(key=lambda x: str(x.get("observed_at")))

                # --- NEW LOGIC ---
                if len(items) == 1:
                    first = items[0]
                    last = items[0]   # flat line
                else:
                    first = items[0]
                    last = items[-1]

                name = last.get("competitor_name") or first.get("competitor_name") or "Unknown"

                first_cnt = _safe_int(first.get("google_review_count"))
                last_cnt = _safe_int(last.get("google_review_count"))

                if first_cnt is None:
                    first_cnt = 0
                if last_cnt is None:
                    last_cnt = 0

                tmp.append(
                    {
                        "competitor_name": name,
                        "google_review_count": last_cnt,
                        "google_rating": last.get("google_rating"),
                        "reviews_delta_7d": last_cnt - first_cnt,
                        "reviews_delta_1d": 0,
                        "rating_delta_7d": None,
                    }
                )

            competitor_deltas = tmp


            if isinstance(schedule_meta, dict) and schedule_meta.get("id"):
                try:
                    schedule_id = UUID(str(schedule_meta["id"]))
                except Exception:
                    schedule_id = UUID("00000000-0000-0000-0000-000000000000")

            title = f"Competitive Report ({days}d) - {now.strftime('%Y-%m-%d')}"

            if not competitor_deltas:
                competitor_deltas = []

        try:
            collect_snapshots_for_business(business_id)
            raw = compute_snapshot_deltas(business_id=business_id, days=days)

            if isinstance(raw, list):
                competitor_deltas = raw
            elif isinstance(raw, dict):
                as_of = raw.get("as_of")
                competitor_deltas = raw.get("deltas") or raw.get("competitors") or []
            else:
                competitor_deltas = []

            competitor_deltas = [d for d in competitor_deltas if isinstance(d, dict)]

        except Exception as e:
            logger.warning("snapshot refresh before report generation failed: %s", e)

            if not competitor_deltas:
                competitor_deltas = [
                    {
                        "competitor_name": business_name or "Your business",
                        "google_review_count": 0,
                        "google_rating": None,
                        "reviews_delta_7d": 0,
                        "reviews_delta_1d": 0,
                        "rating_delta_7d": None,
                    }
                ]

        # -------------------------
        # Top movers
        # -------------------------
        sorted_by_7d = sorted(
            competitor_deltas,
            key=lambda x: int((x.get("reviews_delta_7d") or 0) if x.get("reviews_delta_7d") is not None else 0),
            reverse=True,
        )

        top_moves = [
            {
                "competitor_name": d.get("competitor_name") or d.get("name") or "Unknown",
                "reviews_delta_7d": int(d.get("reviews_delta_7d") or 0) if d.get("reviews_delta_7d") is not None else 0,
                "reviews_delta_1d": int(d.get("reviews_delta_1d") or 0),
                "rating_delta_7d": d.get("rating_delta_7d"),
            }
            for d in sorted_by_7d[:5]
        ]

        insights: List[Dict[str, Any]] = []

        # -------------------------
        # Momentum
        # -------------------------
        momentum_items: List[Dict[str, Any]] = []
        for d in competitor_deltas:
            competitor_name = d.get("competitor_name") or d.get("name") or "Unknown"
            reviews_delta_1d = int(d.get("reviews_delta_1d") or 0)
            reviews_delta_7d = int(d.get("reviews_delta_7d") or 0) if d.get("reviews_delta_7d") is not None else 0

            rating_delta_7d: Optional[float] = d.get("rating_delta_7d")
            try:
                rating_delta_7d = float(rating_delta_7d) if rating_delta_7d is not None else None
            except Exception:
                rating_delta_7d = None

            res = compute_competitor_momentum(
                MomentumInputs(
                    competitor_name=competitor_name,
                    reviews_delta_1d=reviews_delta_1d,
                    reviews_delta_7d=reviews_delta_7d,
                    rating_delta_7d=rating_delta_7d,
                )
            )

            momentum_items.append(
                {
                    "competitor_name": res.competitor_name,
                    "momentum_score": res.momentum_score,
                    "label": res.label,
                    "explanation": res.explanation,
                    "components": res.components,
                }
            )

        momentum_items.sort(key=lambda x: int(x.get("momentum_score") or 0), reverse=True)

        # -------------------------
        # Velocity trends
        # -------------------------
        velocity_trends: List[Dict[str, Any]] = []
        for d in competitor_deltas:
            competitor_name = d.get("competitor_name") or d.get("name") or "Unknown"
            reviews_delta_1d = int(d.get("reviews_delta_1d") or 0)
            reviews_delta_7d = int(d.get("reviews_delta_7d") or 0) if d.get("reviews_delta_7d") is not None else 0

            v = compute_review_velocity_trend(
                ReviewVelocityInputs(
                    competitor_name=competitor_name,
                    reviews_delta_1d=reviews_delta_1d,
                    reviews_delta_7d=reviews_delta_7d,
                )
            )

            velocity_trends.append(
                {
                    "competitor_name": v.competitor_name,
                    "velocity_1d": v.velocity_1d,
                    "velocity_7d_avg": v.velocity_7d_avg,
                    "velocity_ratio": v.velocity_ratio,
                    "anomaly": v.anomaly,
                    "explanation": v.explanation,
                }
            )

        anomaly_rank = {"spike": 0, "drop": 1, "normal": 2}
        velocity_trends.sort(
            key=lambda x: (
                anomaly_rank.get(x.get("anomaly"), 9),
                -float(x.get("velocity_ratio") or 0.0),
            )
        )

        # -------------------------
        # Threat detection
        # -------------------------
        threats: List[Dict[str, Any]] = []
        momentum_by_name = {m.get("competitor_name"): m for m in momentum_items}
        velocity_by_name = {v.get("competitor_name"): v for v in velocity_trends}

        for d in competitor_deltas:
            competitor_name = d.get("competitor_name") or d.get("name") or "Unknown"
            m = momentum_by_name.get(competitor_name) or {}
            v = velocity_by_name.get(competitor_name) or {}

            momentum_score = int(m.get("momentum_score") or 0)
            velocity_ratio = float(v.get("velocity_ratio") or 0.0)
            reviews_delta_7d = int(d.get("reviews_delta_7d") or 0) if d.get("reviews_delta_7d") is not None else 0

            tr = compute_threat(
                ThreatInputs(
                    competitor_name=competitor_name,
                    momentum_score=momentum_score,
                    velocity_ratio=velocity_ratio,
                    reviews_delta_7d=reviews_delta_7d,
                )
            )

            threats.append(
                {
                    "competitor_name": tr.competitor_name,
                    "threat_level": tr.threat_level,
                    "threat_score": tr.threat_score,
                    "reasons": tr.reasons,
                }
            )

        level_rank = {"high": 0, "medium": 1, "low": 2}
        threats.sort(key=lambda x: (level_rank.get(x["threat_level"], 9), -int(x["threat_score"])))

        # -------------------------
        # Share of voice (owner-centric)
        # -------------------------
        business_name = None
        business_reviews_total = None
        owner_competitor_id = None

        try:
            business_obj = get_business_with_competitors(business_id)

            if hasattr(business_obj, "model_dump"):
                business_obj = business_obj.model_dump()
            elif hasattr(business_obj, "dict"):
                business_obj = business_obj.dict()

            b = (business_obj or {}).get("business") or {}
            business_name = b.get("name")

            competitors_meta = (business_obj or {}).get("competitors") or []
            for comp in competitors_meta:
                if hasattr(comp, "model_dump"):
                    try:
                        comp = comp.model_dump()
                    except Exception:
                        continue
                elif hasattr(comp, "dict"):
                    try:
                        comp = comp.dict()
                    except Exception:
                        continue

                if not isinstance(comp, dict):
                    continue

                if comp.get("is_business") is True:
                    owner_competitor_id = comp.get("id")
                    break

            for k in ("google_review_count", "reviews_total", "review_count", "reviews"):
                if b.get(k) is not None:
                    business_reviews_total = int(b.get(k))
                    break
        except Exception:
            business_name = None
            business_reviews_total = None
            owner_competitor_id = None

        try:
            share_of_voice = (
                compute_share_of_voice_from_deltas(
                    competitor_deltas=competitor_deltas,
                    include_business_self=True,
                    business_name=business_name,
                    business_reviews_total=business_reviews_total,
                )
                or {"market_total_reviews": 0, "rows": []}
            )
        except Exception:
            share_of_voice = {"market_total_reviews": 0, "rows": []}

        if hasattr(share_of_voice, "model_dump"):
            share_of_voice = share_of_voice.model_dump()
        elif hasattr(share_of_voice, "dict"):
            share_of_voice = share_of_voice.dict()
        elif not isinstance(share_of_voice, dict):
            share_of_voice = {"market_total_reviews": 0, "rows": []}

        # -------------------------
        # Summary text
        # -------------------------
        summary_text = "Report generated."

        # -------------------------
        # Insights — generation-time
        # -------------------------
        tmp_sections = {
            "momentum": competitor_deltas,
            "insights": insights,
            "share_of_voice": share_of_voice,
        }

        from app.services.insights_service import build_baseline_insights

        baseline_insights = build_baseline_insights(
            share_of_voice,
            previous_share_of_voice=(
                (prev_sections or {}).get("share_of_voice")
                if isinstance(prev_sections, dict)
                else None
            ),
        )

        if "insights" not in tmp_sections or not isinstance(tmp_sections["insights"], list):
            tmp_sections["insights"] = []

        tmp_sections["insights"].extend(baseline_insights)

        add_competitor_surge_insight(tmp_sections, min_review_delta=1, min_ratio=1.1, max_items=2)
        add_market_quiet_insight(tmp_sections, window_days=7)
        add_market_concentration_insight(tmp_sections)
        add_challenger_gap_insight(tmp_sections)
        add_leader_pulling_away_insight(tmp_sections)
        add_competitive_tier_pressure_insight(tmp_sections)

        insights = tmp_sections.get("insights") or insights

        # -------------------------
        # Money insights from legacy review text bucket builder
        # -------------------------
        try:
            owner_reviews, competitor_reviews = _build_review_buckets_from_business_reviews(
                business_id
            )

            money_insights = build_money_insights(
                owner_reviews=owner_reviews,
                competitor_reviews=competitor_reviews,
                owner_name=business_name,
            )

            if money_insights:
                if not isinstance(insights, list):
                    insights = []

                existing_types = {
                    x.get("type")
                    for x in insights
                    if isinstance(x, dict) and x.get("type")
                }

                for mi in money_insights:
                    if not isinstance(mi, dict):
                        continue
                    if mi.get("type") in existing_types:
                        continue
                    insights.append(mi)
                    if mi.get("type"):
                        existing_types.add(mi.get("type"))
        except Exception as e:
            logger.warning("money insights skipped: %s", e)

                # -------------------------
        # Review insights from google_reviews ingestion pipeline
        # -------------------------
        customer_perception_text = ""

        try:
            review_insights = build_review_insights_for_business(
                business_id=str(business_id),
                owner_competitor_id=str(owner_competitor_id) if owner_competitor_id else None,
                owner_name=business_name,
            )

            if review_insights:
                if not isinstance(insights, list):
                    insights = []

                existing_review_types = {
                    (
                        x.get("type"),
                        (x.get("details") or {}).get("competitor_id"),
                        x.get("summary"),
                    )
                    for x in insights
                    if isinstance(x, dict)
                }

                for ri in review_insights:
                    if not isinstance(ri, dict):
                        continue

                    dedupe_key = (
                        ri.get("type"),
                        (ri.get("details") or {}).get("competitor_id"),
                        ri.get("summary"),
                    )
                    if dedupe_key in existing_review_types:
                        continue

                    insights.append(ri)
                    existing_review_types.add(dedupe_key)

                customer_perception_text = format_insights_for_report(
                    review_insights,
                    owner_name=business_name,
                ) or ""
        except Exception as e:
            logger.warning("review insights skipped: %s", e)

        sections = {
            "top_moves": top_moves,
            "insights": insights,
            "momentum": momentum_items,
            "velocity_trends": velocity_trends,
            "threats": threats,
            "share_of_voice": share_of_voice,
            "customer_perception_insights": {
                "title": "Customer Perception Insights",
                "body": customer_perception_text,
            },
        }

        if not customer_perception_text:
            customer_perception_text = (
                "Recent customer review signals show where competitors are gaining trust, "
                "where friction is appearing, and which themes should shape this month’s positioning. "
                "Use these signals to tighten messaging, improve weak spots, and reinforce the strongest reasons customers choose you."
            )

        if not any(isinstance(i, dict) and i.get("type") == "customer_perception" for i in insights):
            insights.append({
                "type": "customer_perception",
                "summary": customer_perception_text,
                "severity": "info",
                "details": {
                    "source": "fallback_customer_perception",
                },
            })

        suppress_market_quiet_if_owner_centric(sections)
        add_weekly_actions_insight(sections)

        insights = sections.get("insights") if isinstance(sections.get("insights"), list) else []
        sections["insights"] = insights

        try:
            friction_reviews = get_review_rows_for_business(str(business_id)) or []

            friction_counts = build_review_theme_counts(
                friction_reviews,
                owner_competitor_id=str(owner_competitor_id) if owner_competitor_id else None,
            )

            friction_insights = build_customer_friction_insights(friction_counts)
            friction_summary = build_customer_friction_summary(
                friction_counts,
                friction_insights,
            )

            sections["customer_friction_signals"] = {
                "title": "Customer Friction Signals",
                "subtitle": "Repeated complaint themes found in negative review text.",
                "summary": friction_summary,
                "themes": friction_counts.get("themes") or [],
                "competitors": friction_counts.get("competitors") or [],
                "owner_top_themes": friction_counts.get("owner_top_themes") or [],
                "insights": friction_insights,
            }

            themes = (sections.get("customer_friction_signals") or {}).get("themes") or []

            if themes:
                top_theme = themes[0]

                customer_perception_text = (
                    f"Customer feedback is currently driven by {top_theme}. "
                    "This theme is shaping how competitors are perceived and influencing customer decision-making. "
                    "Strengthening this area while positioning against competitor weaknesses will improve conversion."
                )

                sections["customer_perception_insights"]["body"] = customer_perception_text

        except Exception as e:
            logger.warning("customer friction signals skipped: %s", e)
            sections["customer_friction_signals"] = {
                "title": "Customer Friction Signals",
                "subtitle": "Repeated complaint themes found in negative review text.",
                "summary": "Customer friction signals were not available for this report.",
                "themes": [],
                "competitors": [],
                "owner_top_themes": [],
                "insights": [],
            }


        previous_insights = []
        if isinstance(prev_sections, dict):
            previous_insights = prev_sections.get("insights") or []

            sections["report_experience"] = _build_report_experience_payload(
            insights,
            previous_insights=previous_insights,
            sections=sections,
        )

        if not sections.get("report_experience"):
            sections["report_experience"] = {}

        focus = sections["report_experience"].get("this_month_focus") or []

        if not focus:
            focus = insights[:3]

        if not focus:
            focus = [
                {
                    "type": "fallback_focus",
                    "summary": "Maintain review momentum and watch competitor movement this month.",
                    "severity": "info",
                    "details": {},
                }
            ]

        sections["report_experience"]["this_month_focus"] = focus[:3]

        if not sections["report_experience"].get("immediate_priorities"):
            sections["report_experience"]["immediate_priorities"] = len(focus[:3]) or 1

        sections["share_of_voice_donut"] = _build_share_of_voice_donut_payload(sections)
        sections["review_count_bar"] = _build_review_count_bar_payload(sections)
        sections["review_pulse"] = _build_review_pulse_payload(business_id)
        premium_headline = build_executive_headline(sections)

        if sections.get("report_experience"):
            sections["report_experience"]["summary_text"] = premium_headline

        summary_text = premium_headline

        print("\n=== REPORT DEBUG ===")
        print("competitor_deltas:", len(competitor_deltas))
        print("insights:", len(sections.get("insights", [])))
        print("money_insights:", len([i for i in sections.get("insights", []) if i.get("type") == "money"]))
        print("review_insights:", len([i for i in sections.get("insights", []) if i.get("type") == "review"]))
        print("customer_friction:", len((sections.get("customer_friction_signals") or {}).get("insights", [])))
        print("this_month_focus:", len((sections.get("report_experience") or {}).get("this_month_focus", [])))
        print("review_pulse:", bool(sections.get("review_pulse")))
        print("====================\n")

        created_any = insert_generated_report(
            business_id=business_id,
            schedule_id=schedule_id,
            period_start=period_start,
            period_end=period_end,
            status="generated",
            title=title,
            summary_text=summary_text,
            sections=sections,
            inputs={
                "source": "compute_snapshot_deltas",
                "days": days,
                "as_of": as_of,
                "count": len(competitor_deltas),
                "schedule": schedule_meta,
            },
            error=None,
        )

        created = _as_dict(created_any)
        created_id_val = created.get("id")

        try:
            created_id = UUID(str(created_id_val)) if created_id_val else None
        except Exception:
            created_id = None

        # ✅ Step 5/6: compare PREVIOUS (pre-insert) vs THIS new report
        if prev_sections and isinstance(prev_sections, dict) and isinstance(sections, dict) and created_id:
            pc = build_position_change_insight(prev_sections, sections)

            mm = build_market_movers_insight(
                prev_sections,
                sections,
                min_share_delta_pp=0.1,
                min_review_delta=1,
            )

            if not pc and not mm:
                owner_name_for_flat = business_name
                owner_rank = None

                try:
                    sov = sections.get("share_of_voice") or {}
                    rows = sov.get("rows") or []

                    for i, r in enumerate(rows):
                        name = r.get("name") or r.get("competitor_name")
                        if (
                            name
                            and owner_name_for_flat
                            and str(name).strip().lower() == str(owner_name_for_flat).strip().lower()
                        ):
                            owner_rank = i + 1
                            break
                except Exception:
                    owner_rank = None

                if owner_rank:
                    summary = f"Market was mostly flat versus the prior report. {owner_name_for_flat} held position #{owner_rank}."
                else:
                    summary = "Market was mostly flat versus the prior report."

                mm = {
                    "type": "market_movers",
                    "summary": summary,
                    "details": {
                        "flat_comparison": True,
                        "owner_rank": owner_rank,
                    },
                    "severity": "info",
                }

            previous_insights = []
            if isinstance(prev_sections, dict):
                previous_insights = prev_sections.get("insights") or []

            if pc:
                created.setdefault("sections", {}).setdefault("insights", []).append(pc)
                created["sections"]["report_experience"] = _build_report_experience_payload(
                    created["sections"].get("insights"),
                    previous_insights=previous_insights,
                    sections=created.get("sections") or {},
                )
                _append_insight_to_report_in_db(
                    created_id,
                    pc,
                    previous_insights=previous_insights,
                )

            if mm:
                created.setdefault("sections", {}).setdefault("insights", []).append(mm)
                created["sections"]["report_experience"] = _build_report_experience_payload(
                    created["sections"].get("insights"),
                    previous_insights=previous_insights,
                    sections=created.get("sections") or {},
                )
                _append_insight_to_report_in_db(
                    created_id,
                    mm,
                    previous_insights=previous_insights,
                )

        sections = created.get("sections") or {}

        if not sections.get("report_experience"):
            sections["report_experience"] = {}

        focus = sections["report_experience"].get("this_month_focus") or []
        insights = sections.get("insights") or []

        if not focus:
            focus = insights[:3]

        if not focus:
            focus = [{
                "type": "fallback_focus",
                "summary": "Increase review velocity this month and strengthen positioning against the current review-share leaders.",
                "severity": "info",
                "details": {},
            }]

        sections["report_experience"]["this_month_focus"] = focus[:3]
        sections["report_experience"]["immediate_priorities"] = len(focus[:3]) or 1
        created["sections"] = sections

        return created

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate report: {str(e)}")