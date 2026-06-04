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
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Header, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.services.snapshot_service import collect_snapshots_for_business
from app.services.report_integrity_service import apply_report_integrity_rules

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

import os

def verify_admin_key(x_admin_key: str = Header(None)):
    expected = os.getenv("ADMIN_API_KEY")
    if not expected or x_admin_key != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")

logger = logging.getLogger(__name__)

router = APIRouter()
router.include_router(analytics_router)
router.include_router(report_schedules_router)
router.include_router(generated_reports_router)
router.include_router(review_ingestion_router)


class CreateCheckoutSessionIn(BaseModel):
    business_id: UUID
    plan: str


class SubscribeIn(BaseModel):
    plan: str                        # "starter" or "growth"
    contact_name: str
    contact_email: str
    contact_phone: str = ""
    business_name: str
    city: str
    state: str
    competitor_names: list[str] = []


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


def _send_admin_alert(subject: str, body: str) -> None:
    """Send a plain-text alert email to the admin address. Never raises."""
    try:
        from app.services.email_service import send_plain_email
        send_plain_email(
            to_email="craigw0503@gmail.com",
            subject=subject,
            body=body,
        )
    except Exception as exc:
        logger.warning("Admin alert email failed: %s", exc)


def _disable_report_schedule(business_id: str) -> None:
    """Disable the report schedule for a business when their subscription ends or payment fails."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE report_schedules
                    SET is_enabled = false, next_run_at = NULL
                    WHERE business_id = %s
                    """,
                    (str(business_id),),
                )
            conn.commit()
        logger.info("Report schedule disabled for business %s", business_id)
    except Exception as exc:
        logger.warning("Could not disable report schedule for %s: %s", business_id, exc)


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

    # Derive current_period_end from subscription items
    items = (((subscription.get("items") or {}).get("data")) or [])

    current_period_end = None
    if items:
        current_period_end = items[0].get("current_period_end")

    current_period_end_dt = None
    if current_period_end:
        try:
            current_period_end_dt = datetime.fromtimestamp(
                int(current_period_end),
                tz=timezone.utc,
            )
        except (TypeError, ValueError):
            current_period_end_dt = None

    _upsert_business_billing_state(
        business_id=str(business_id),
        stripe_customer_id=str(customer_id) if customer_id else None,
        stripe_subscription_id=str(subscription_id) if subscription_id else None,
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
        # Calculate trial end = 1st of next month at midnight UTC
        # Subscriber gets their baseline report now, first charge hits on the 1st
        _now = datetime.now(timezone.utc)
        if _now.month == 12:
            _trial_end = datetime(_now.year + 1, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        else:
            _trial_end = datetime(_now.year, _now.month + 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        _trial_end_ts = int(_trial_end.timestamp())

        session_kwargs = {
            "mode": "subscription",
            "success_url": settings.stripe_success_url,
            "cancel_url": settings.stripe_cancel_url,
            "allow_promotion_codes": True,
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
                "trial_end": _trial_end_ts,
                "metadata": {
                    "business_id": business_id,
                    "plan": plan,
                },
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


@router.get("/billing/portal/{business_id}")
def billing_portal(business_id: UUID):
    """
    Creates a Stripe Customer Portal session for the given business and redirects
    the subscriber there so they can manage or cancel their subscription.
    """
    from fastapi.responses import RedirectResponse

    if not settings.stripe_secret_key:
        raise HTTPException(status_code=500, detail="Missing STRIPE_SECRET_KEY")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT stripe_customer_id, stripe_subscription_id, name FROM businesses WHERE id = %s",
                (str(business_id),),
            )
            row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Business not found")

    stripe_customer_id = (row.get("stripe_customer_id") or "").strip()

    # If customer ID not stored, look it up from the subscription
    if not stripe_customer_id:
        sub_id = (row.get("stripe_subscription_id") or "").strip()
        if sub_id:
            try:
                stripe.api_key = settings.stripe_secret_key
                sub = stripe.Subscription.retrieve(sub_id)
                stripe_customer_id = sub.get("customer") or ""
            except Exception:
                pass

    if not stripe_customer_id:
        from fastapi.responses import HTMLResponse as _HTMLResponse
        return _HTMLResponse("""
        <html><body style="font-family:Arial,sans-serif;max-width:500px;margin:60px auto;text-align:center;color:#172033;">
        <h2>Manage Your Subscription</h2>
        <p>To cancel or modify your subscription, please email us at
        <a href="mailto:support@pulselci.com">support@pulselci.com</a>
        and we'll take care of it within one business day.</p>
        </body></html>
        """, status_code=200)

    try:
        stripe.api_key = settings.stripe_secret_key
        portal_session = stripe.billing_portal.Session.create(
            customer=stripe_customer_id,
            return_url=settings.stripe_cancel_url or "https://pulselci.com",
        )
        return RedirectResponse(url=portal_session.url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not create portal session: {e}")


@router.post("/billing/subscribe")
def subscribe(payload: SubscribeIn):
    """
    New subscriber flow from the website pricing page.
    1. Run prospect onboarding (create business + ingest data)
    2. Create a Stripe Checkout session for the chosen plan
    3. Return the Stripe checkout URL to redirect the browser
    """
    from app.services.prospect_onboarding_service import onboard_prospect

    # Step 1 — onboard
    result = onboard_prospect(
        contact_name=payload.contact_name,
        contact_email=payload.contact_email,
        contact_phone=payload.contact_phone,
        business_name=payload.business_name,
        city=payload.city,
        state=payload.state,
        competitor_names=payload.competitor_names,
        skip_report=True,  # Webhook generates full report after payment confirms
    )

    if not result.ok or not result.business_id:
        raise HTTPException(status_code=500, detail=result.error or "Onboarding failed")

    # Step 2 — create Stripe checkout session
    checkout = create_checkout_session(
        CreateCheckoutSessionIn(
            business_id=UUID(result.business_id),
            plan=payload.plan,
        )
    )

    return {
        "ok": True,
        "business_id": result.business_id,
        "checkout_url": checkout.get("url"),
    }


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
                    is_active=_is_billing_active(status),
                )

                # ── Enable schedule + set next run to 1st of next month ──
                try:
                    from app.services.report_periods import Schedule, compute_next_run_at
                    from app.services.report_schedule_service import upsert_schedule_for_business
                    from datetime import date
                    import calendar

                    now = datetime.now(timezone.utc)
                    # First day of next month at 8am ET
                    if now.month == 12:
                        first_next = datetime(now.year + 1, 1, 1, 8, 0, tzinfo=timezone.utc)
                    else:
                        first_next = datetime(now.year, now.month + 1, 1, 8, 0, tzinfo=timezone.utc)

                    upsert_schedule_for_business(
                        UUID(str(business_id)),
                        frequency="monthly",
                        day_of_week=None,
                        day_of_month=1,
                        hour=8,
                        minute=0,
                        timezone="America/New_York",
                        is_enabled=True,
                        next_run_at=first_next,
                    )
                    logger.info("Schedule enabled for new subscriber %s, next run %s", business_id, first_next)
                except Exception as exc:
                    logger.warning("Could not enable schedule for %s: %s", business_id, exc)

                # ── Generate and email first report immediately ──
                try:
                    report = generate_business_report(UUID(str(business_id)))
                    if hasattr(report, "model_dump"):
                        report_dict = report.model_dump()
                    elif hasattr(report, "dict"):
                        report_dict = report.dict()
                    else:
                        report_dict = report if isinstance(report, dict) else {}

                    report_id = report_dict.get("id")

                    # Explicitly mark as NOT free preview (set false, don't just remove)
                    if report_id:
                        with get_conn() as conn:
                            with conn.cursor() as cur:
                                cur.execute(
                                    """
                                    UPDATE generated_reports
                                    SET sections = sections || '{"is_free_preview": false}'::jsonb
                                    WHERE id = %s
                                    """,
                                    (str(report_id),),
                                )
                            conn.commit()

                        # Look up contact email and name from business notes
                        contact_email = None
                        contact_name = "there"
                        business_name_val = ""
                        with get_conn() as conn:
                            with conn.cursor() as cur:
                                cur.execute(
                                    "SELECT name, notes FROM businesses WHERE id = %s",
                                    (str(business_id),),
                                )
                                biz = cur.fetchone()
                                if biz:
                                    business_name_val = biz.get("name") or ""
                                    notes = biz.get("notes") or ""
                                    # Extract email from notes: "Contact: Name <email>"
                                    import re as _re
                                    m = _re.search(r'<([^>]+@[^>]+)>', notes)
                                    if m:
                                        contact_email = m.group(1)
                                    m2 = _re.search(r'Contact:\s*([^<\n]+)', notes)
                                    if m2:
                                        contact_name = m2.group(1).strip().split()[0]

                        if contact_email:
                            from app.api.generated_reports import send_generated_report_email, SendReportRequest
                            send_generated_report_email(
                                UUID(str(report_id)),
                                SendReportRequest(
                                    to_email=contact_email,
                                    subject=f"Your Pulse LCI Report is Ready — {business_name_val}",
                                    body_text=(
                                        f"Hi {contact_name},\n\n"
                                        "Your first Pulse LCI competitive intelligence report is attached. "
                                        "It shows your current market position, how you compare to your competitors, "
                                        "and your top priorities for this month.\n\n"
                                        "Your next report will arrive on the 1st of next month.\n\n"
                                        "— Pulse LCI"
                                    ),
                                ),
                            )
                            logger.info("First report emailed to %s for business %s", contact_email, business_id)

                except Exception as exc:
                    logger.error("Could not generate/email first report for %s: %s", business_id, exc)

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

                # Disable report schedule when subscription is cancelled or lapses
                if event_type == "customer.subscription.deleted" or not _is_billing_active(status):
                    _disable_report_schedule(str(business_id))

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

                # Derive current_period_end from subscription items
                items = (((subscription.get("items") or {}).get("data")) or [])

                current_period_end = None
                if items:
                    current_period_end = items[0].get("current_period_end")

                current_period_end_dt = None
                if current_period_end:
                    try:
                        current_period_end_dt = datetime.fromtimestamp(
                            int(current_period_end),
                            tz=timezone.utc,
                        )
                    except (TypeError, ValueError):
                        current_period_end_dt = None

                _upsert_business_billing_state(
                    business_id=str(business_id),
                    stripe_customer_id=str(customer_id) if customer_id else None,
                    stripe_subscription_id=str(subscription_id) if subscription_id else None,
                    stripe_price_id=str(price_id) if price_id else None,
                    billing_status=str(status) if status else None,
                    billing_current_period_end=current_period_end_dt,
                    is_active=_is_billing_active(status),
                )

                # Disable schedule so failed-payment businesses stop receiving reports
                if business_id:
                    _disable_report_schedule(str(business_id))

                # Send branded payment failed email to the customer
                if business_id:
                    try:
                        import re as _re
                        with get_conn() as conn:
                            with conn.cursor() as cur:
                                cur.execute(
                                    "SELECT name, notes FROM businesses WHERE id = %s",
                                    (str(business_id),),
                                )
                                biz = cur.fetchone()

                        if biz:
                            biz_name = biz.get("name") or "your business"
                            notes = biz.get("notes") or ""
                            m_email = _re.search(r'<([^>]+@[^>]+)>', notes)
                            m_name = _re.search(r'Contact:\s*([^<\n]+)', notes)
                            contact_email = m_email.group(1) if m_email else None
                            contact_first = m_name.group(1).strip().split()[0] if m_name else "there"

                            if contact_email:
                                from app.services.email_service import send_plain_email
                                send_plain_email(
                                    to_email=contact_email,
                                    subject=f"Action needed — payment failed for {biz_name}",
                                    body=(
                                        f"Hi {contact_first},\n\n"
                                        f"We weren't able to process your payment for your Pulse LCI subscription for {biz_name}.\n\n"
                                        "Your monthly reports have been paused until payment is resolved.\n\n"
                                        "To update your payment method and reactivate your subscription, "
                                        f"visit: https://pulse-lci-api.onrender.com/billing/portal/{business_id}\n\n"
                                        "If you have any questions, reply to this email or reach us at reports@pulselci.com.\n\n"
                                        "— Pulse LCI"
                                    ),
                                )
                                logger.info("Payment failed email sent to %s for business %s", contact_email, business_id)
                    except Exception as exc:
                        logger.warning("Could not send payment failed email for %s: %s", business_id, exc)


    except Exception as e:
        logger.exception("Stripe webhook handler failed")
        raise HTTPException(status_code=500, detail=f"Webhook processing failed: {e}")

    return {"received": True, "type": event_type}


@router.get("/billing/checkout-success")
def billing_checkout_success(session_id: str, redirect: str | None = None):
    """
    Called by Stripe success URL redirect.
    Retrieves the session, enables the schedule, generates + emails the full report,
    then redirects the browser to the welcome page.
    """
    from fastapi.responses import RedirectResponse

    welcome_url = redirect or "https://pulselci.com/welcome.html"

    if not settings.stripe_secret_key:
        return RedirectResponse(url=welcome_url)

    stripe.api_key = settings.stripe_secret_key

    try:
        print(f"[CHECKOUT-SUCCESS] session_id={session_id}", flush=True)
        session = _stripe_obj_to_dict(stripe.checkout.Session.retrieve(session_id))
        business_id = session.get("client_reference_id")
        print(f"[CHECKOUT-SUCCESS] business_id={business_id}", flush=True)

        if not business_id:
            print(f"[CHECKOUT-SUCCESS] ERROR: no business_id in session {session_id}", flush=True)
            return RedirectResponse(url=welcome_url)

        _activate_subscriber(business_id)
        print(f"[CHECKOUT-SUCCESS] activation complete for {business_id}", flush=True)

    except Exception as exc:
        import traceback
        print(f"[CHECKOUT-SUCCESS] ERROR: {exc}\n{traceback.format_exc()}", flush=True)

    return RedirectResponse(url=welcome_url)


def _activate_subscriber(business_id: str):
    """Enable schedule, generate full report, email it."""
    import re as _re
    print(f"[ACTIVATE] START business_id={business_id}", flush=True)

    # 1. Enable schedule
    now = datetime.now(timezone.utc)
    first_next = datetime(now.year if now.month < 12 else now.year + 1,
                         now.month + 1 if now.month < 12 else 1,
                         1, 8, 0, tzinfo=timezone.utc)
    upsert_schedule_for_business(
        UUID(business_id),
        frequency="monthly", day_of_week=None, day_of_month=1,
        hour=8, minute=0, timezone="America/New_York",
        is_enabled=True, next_run_at=first_next,
    )
    logger.info("Schedule enabled for %s", business_id)

    # 2. Generate full report (schedule is enabled so is_free_preview=False)
    report = generate_business_report(UUID(business_id))
    if hasattr(report, "model_dump"):
        report_dict = report.model_dump()
    elif hasattr(report, "dict"):
        report_dict = report.dict()
    else:
        report_dict = report if isinstance(report, dict) else {}

    report_id = str(report_dict.get("id") or "")
    if not report_id:
        logger.error("Report generation failed for subscriber %s", business_id)
        return

    # 3. Force is_free_preview=false
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE generated_reports SET sections = sections || '{\"is_free_preview\": false}'::jsonb WHERE id = %s",
                (report_id,),
            )
        conn.commit()
    logger.info("Report %s marked as full (not preview)", report_id)

    # 4. Look up contact info
    contact_email = None
    contact_name = "there"
    business_name_val = ""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT name, notes FROM businesses WHERE id = %s", (business_id,))
            biz = cur.fetchone()
            if biz:
                business_name_val = biz.get("name") or ""
                notes = biz.get("notes") or ""
                m = _re.search(r'<([^>]+@[^>]+)>', notes)
                if m:
                    contact_email = m.group(1)
                m2 = _re.search(r'Contact:\s*([^<\n]+)', notes)
                if m2:
                    contact_name = m2.group(1).strip().split()[0]

    if not contact_email:
        logger.warning("No email found for subscriber %s", business_id)
        return

    # 5. Email full report
    print(f"[ACTIVATE] emailing report {report_id} to {contact_email}", flush=True)
    from app.api.generated_reports import send_generated_report_email, SendReportRequest
    send_generated_report_email(
        UUID(report_id),
        SendReportRequest(
            to_email=contact_email,
            subject=f"Your Pulse LCI Report is Ready — {business_name_val}",
            body_text=(
                f"Hi {contact_name},\n\n"
                "Your first Pulse LCI competitive intelligence report is attached. "
                "It shows your current market position, how you compare to competitors, "
                "and your top priorities for this month.\n\n"
                "Your next report will arrive on the 1st of every month.\n\n"
                "— Pulse LCI"
            ),
        ),
    )
    print(f"[ACTIVATE] DONE — full report emailed to {contact_email} for {business_id}", flush=True)


@router.post("/admin/business/{business_id}/send-full-report")
def admin_send_full_report(business_id: str, x_admin_key: str = Header(None)):
    """
    Regenerate a full (unblurred) report for a subscriber and email it.
    Use when a subscriber received a blurred free-preview report by mistake.
    """
    if x_admin_key != settings.ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")

    import re as _re

    # 1. Enable schedule (ensure is_enabled=True)
    try:
        from datetime import date as _date
        now = datetime.now(timezone.utc)
        if now.month == 12:
            first_next = datetime(now.year + 1, 1, 1, 8, 0, tzinfo=timezone.utc)
        else:
            first_next = datetime(now.year, now.month + 1, 1, 8, 0, tzinfo=timezone.utc)
        upsert_schedule_for_business(
            UUID(business_id),
            frequency="monthly", day_of_week=None, day_of_month=1,
            hour=8, minute=0, timezone="America/New_York",
            is_enabled=True, next_run_at=first_next,
        )
    except Exception as exc:
        logger.warning("Could not enable schedule for %s: %s", business_id, exc)

    # 2. Generate fresh report (schedule is now enabled so is_free_preview=False)
    report = generate_business_report(UUID(business_id))
    if hasattr(report, "model_dump"):
        report_dict = report.model_dump()
    elif hasattr(report, "dict"):
        report_dict = report.dict()
    else:
        report_dict = report if isinstance(report, dict) else {}

    report_id = str(report_dict.get("id") or "")
    if not report_id:
        raise HTTPException(status_code=500, detail="Report generation failed")

    # 3. Force is_free_preview=false in DB
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE generated_reports SET sections = sections || '{\"is_free_preview\": false}'::jsonb WHERE id = %s",
                (report_id,),
            )
        conn.commit()

    # 4. Look up contact info from business notes
    contact_email = None
    contact_name = "there"
    business_name_val = ""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT name, notes FROM businesses WHERE id = %s", (business_id,))
            biz = cur.fetchone()
            if biz:
                business_name_val = biz.get("name") or ""
                notes = biz.get("notes") or ""
                m = _re.search(r'<([^>]+@[^>]+)>', notes)
                if m:
                    contact_email = m.group(1)
                m2 = _re.search(r'Contact:\s*([^<\n]+)', notes)
                if m2:
                    contact_name = m2.group(1).strip().split()[0]

    if not contact_email:
        return {"ok": True, "report_id": report_id, "emailed": False, "reason": "No email found in business notes"}

    # 5. Email full report
    from app.api.generated_reports import send_generated_report_email, SendReportRequest
    send_generated_report_email(
        UUID(report_id),
        SendReportRequest(
            to_email=contact_email,
            subject=f"Your Pulse LCI Report — {business_name_val}",
            body_text=(
                f"Hi {contact_name},\n\n"
                "Your full Pulse LCI competitive intelligence report is attached.\n\n"
                "You'll receive an updated report on the 1st of every month.\n\n"
                "— Pulse LCI"
            ),
        ),
    )

    return {"ok": True, "report_id": report_id, "emailed_to": contact_email}


@router.get("/admin/clients", response_class=HTMLResponse)
def admin_clients_dashboard(key: str = ""):
    """
    Clean client tracking dashboard.
    Access: https://pulse-lci-api.onrender.com/admin/clients?key=YOUR_ADMIN_KEY
    """
    import re as _re

    if key != settings.ADMIN_API_KEY:
        return HTMLResponse("<h2>Unauthorized</h2>", status_code=401)

    # Fetch all businesses with their schedule state
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    b.id,
                    b.name,
                    b.city,
                    b.state,
                    b.notes,
                    b.created_at,
                    b.stripe_price_id,
                    rs.is_enabled,
                    rs.next_run_at,
                    rs.last_run_at,
                    (
                        SELECT MAX(gr.generated_at)
                        FROM public.generated_reports gr
                        WHERE gr.business_id = b.id
                    ) AS last_report_at,
                    (
                        SELECT COUNT(*)
                        FROM public.generated_reports gr
                        WHERE gr.business_id = b.id
                    ) AS report_count,
                    (
                        SELECT gr.id
                        FROM public.generated_reports gr
                        WHERE gr.business_id = b.id
                        ORDER BY gr.generated_at DESC
                        LIMIT 1
                    ) AS last_report_id
                FROM public.businesses b
                LEFT JOIN public.report_schedules rs ON rs.business_id = b.id
                ORDER BY b.created_at DESC
            """)
            rows = cur.fetchall()

    def extract_email(notes):
        if not notes:
            return ""
        m = _re.search(r'<([^>]+@[^>]+)>', notes or "")
        return m.group(1) if m else ""

    def extract_name(notes):
        if not notes:
            return ""
        m = _re.search(r'Contact:\s*([^<\n]+)', notes or "")
        return m.group(1).strip() if m else ""

    def fmt_dt(dt):
        if not dt:
            return "—"
        try:
            return dt.strftime("%b %d, %Y")
        except Exception:
            return str(dt)[:10]

    prospects = []
    subscribers = []

    for row in rows:
        is_enabled = row.get("is_enabled")
        contact_email = extract_email(row.get("notes") or "")
        contact_name = extract_name(row.get("notes") or "")

        # Map stripe_price_id to plan name
        price_id = row.get("stripe_price_id") or ""
        if price_id == (settings.stripe_price_growth or "__none__"):
            plan_name = "Growth"
        elif price_id == (settings.stripe_price_starter or "____"):
            plan_name = "Starter"
        elif price_id:
            plan_name = "Paid"
        else:
            plan_name = "—"

        entry = {
            "id": str(row.get("id") or ""),
            "name": row.get("name") or "—",
            "city": row.get("city") or "—",
            "state": row.get("state") or "—",
            "contact_name": contact_name,
            "contact_email": contact_email,
            "created_at": fmt_dt(row.get("created_at")),
            "last_report_at": fmt_dt(row.get("last_report_at")),
            "next_run_at": fmt_dt(row.get("next_run_at")),
            "report_count": int(row.get("report_count") or 0),
            "plan": plan_name,
            "last_report_id": str(row.get("last_report_id") or ""),
        }

        if is_enabled:
            subscribers.append(entry)
        else:
            prospects.append(entry)

    def table_rows(entries, show_next=False):
        if not entries:
            return '<tr><td colspan="9" style="text-align:center;color:#94a3b8;padding:20px;">No records yet</td></tr>'
        html = ""
        for e in entries:
            next_col = f'<td>{e["next_run_at"]}</td>' if show_next else '<td>—</td>'
            plan = e.get("plan", "—")
            plan_color = "#166534" if plan == "Growth" else "#1e40af" if plan == "Starter" else "#64748b"
            plan_badge = f'<span style="background:#f0f9ff;color:{plan_color};font-size:11px;font-weight:700;padding:2px 8px;border-radius:99px;">{plan}</span>'
            report_id = e.get("last_report_id", "")
            report_link = (
                f'<a href="/generated-reports/{report_id}/pdf" target="_blank" '
                f'style="color:#2563eb;font-size:12px;">View PDF</a>'
                if report_id else "—"
            )
            html += (
                "<tr>"
                f'<td>{e["name"]}</td>'
                f'<td>{e["city"]}, {e["state"]}</td>'
                f'<td>{e["contact_name"]}</td>'
                f'<td><a href="mailto:{e["contact_email"]}" style="color:#2563eb;">{e["contact_email"]}</a></td>'
                f'<td>{plan_badge}</td>'
                f'<td>{e["created_at"]}</td>'
                f'<td>{e["last_report_at"]}</td>'
                f'<td>{e["report_count"]} sent</td>'
                f'<td>{report_link}</td>'
                + next_col +
                "</tr>"
            )
        return html

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>Pulse LCI — Client Dashboard</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f1f5f9; color: #1e293b; }}
  .header {{ background: #10233f; color: white; padding: 20px 32px; display: flex; align-items: center; justify-content: space-between; }}
  .header h1 {{ font-size: 18px; font-weight: 800; letter-spacing: -0.02em; }}
  .header span {{ font-size: 13px; opacity: 0.6; }}
  .body {{ padding: 28px 32px; max-width: 1200px; margin: 0 auto; }}
  .section {{ background: white; border-radius: 12px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); margin-bottom: 28px; overflow: hidden; }}
  .section-header {{ padding: 16px 20px; border-bottom: 1px solid #e2e8f0; display: flex; align-items: center; gap: 10px; }}
  .section-header h2 {{ font-size: 14px; font-weight: 700; color: #10233f; }}
  .badge {{ padding: 3px 10px; border-radius: 99px; font-size: 12px; font-weight: 700; }}
  .badge-prospect {{ background: #fef9c3; color: #854d0e; }}
  .badge-sub {{ background: #dcfce7; color: #166534; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ padding: 10px 16px; text-align: left; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #64748b; background: #f8fafc; border-bottom: 1px solid #e2e8f0; }}
  td {{ padding: 12px 16px; border-bottom: 1px solid #f1f5f9; color: #334155; vertical-align: middle; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: #f8fafc; }}
  .stats {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 28px; }}
  .stat {{ background: white; border-radius: 10px; padding: 18px 20px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }}
  .stat-val {{ font-size: 28px; font-weight: 900; color: #10233f; }}
  .stat-label {{ font-size: 12px; color: #64748b; margin-top: 4px; }}
</style>
</head>
<body>
<div class="header">
  <h1>Pulse LCI — Client Dashboard</h1>
  <span>Generated {fmt_dt(__import__('datetime').datetime.utcnow())}</span>
</div>
<div class="body">
  <div class="stats">
    <div class="stat"><div class="stat-val">{len(subscribers)}</div><div class="stat-label">Active Subscribers</div></div>
    <div class="stat"><div class="stat-val">{len(prospects)}</div><div class="stat-label">Free Report Prospects</div></div>
    <div class="stat"><div class="stat-val">{len(subscribers) + len(prospects)}</div><div class="stat-label">Total in System</div></div>
  </div>

  <div class="section">
    <div class="section-header">
      <h2>Active Subscribers</h2>
      <span class="badge badge-sub">{len(subscribers)} paying</span>
    </div>
    <table>
      <thead><tr>
        <th>Business</th><th>Location</th><th>Contact</th><th>Email</th><th>Plan</th>
        <th>Signed Up</th><th>Last Report</th><th>Reports Sent</th><th>Last Report</th><th>Next Report</th>
      </tr></thead>
      <tbody>{table_rows(subscribers, show_next=True)}</tbody>
    </table>
  </div>

  <div class="section">
    <div class="section-header">
      <h2>Free Report Prospects</h2>
      <span class="badge badge-prospect">{len(prospects)} prospects</span>
    </div>
    <table>
      <thead><tr>
        <th>Business</th><th>Location</th><th>Contact</th><th>Email</th><th>Plan</th>
        <th>Requested</th><th>Last Report</th><th>Reports Sent</th><th>Last Report</th><th></th>
      </tr></thead>
      <tbody>{table_rows(prospects, show_next=False)}</tbody>
    </table>
  </div>
</div>
</body>
</html>"""

    return HTMLResponse(html)


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
            customer_label=payload.business.customer_label or "customers",
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
            # generate_business_report handles data collection, insight building,
            # chart rendering, DB insert, and post-insert comparisons in one call.
            first_report = generate_business_report(UUID(str(business_id)))
            if hasattr(first_report, "model_dump"):
                first_report = first_report.model_dump()
            elif hasattr(first_report, "dict"):
                first_report = first_report.dict()

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
                    send_plain_email(
                        to_email=recipient.email,
                        subject="Activate your Pulse LCI account",
                        body=(
                            "Hi,\n\n"
                            "Your Pulse Local Competitor Intelligence account is ready.\n\n"
                            f"Activate your subscription here:\n{checkout_url}\n\n"
                            "Once completed, your monthly reports will begin automatically.\n\n"
                            "Thanks,\nPulse LCI"
                        ),
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
def list_admin_businesses(admin_ok: None = Depends(verify_admin_key)):
    sql = """
    select
        b.id,
        b.name,
        b.primary_domain,
        b.city,
        b.state,
        b.country,
        b.notes,
        'customers' as customer_label,

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
                "customer_label": row["customer_label"] or "customers",
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


        # Pull rating from whatever field is available
        raw_rating = (
            row.get("google_rating")
            or row.get("rating")
            or row.get("avg_rating")
        )
        try:
            google_rating = round(float(raw_rating), 1) if raw_rating is not None else None
        except (TypeError, ValueError):
            google_rating = None

        valid_rows.append(
            {
                "competitor_name": competitor_name,
                "reviews_total": reviews_total,
                "share_pct": share_pct,
                "is_business": is_business,
                "google_rating": google_rating,
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
    any_nonzero_delta = False

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

        if any(v != 0 for v in y_values):
            any_nonzero_delta = True

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
        "is_baseline": not any_nonzero_delta,
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


# ---------------------------------------------------------------------------
# Data-driven Execution Plan
# ---------------------------------------------------------------------------

def _build_data_driven_execution_plan(sections: dict) -> list[dict]:
    """
    Builds 3 concrete, numbered action items using real data from the report:
      1. Review gap / momentum item (SOV + top mover)
      2. Competitive positioning item (praise words + rating gap)
      3. Exploit competitor weakness item (friction themes)
    """
    sov = sections.get("share_of_voice") or {}
    sov_rows = sov.get("rows") or []

    owner = next((r for r in sov_rows if r.get("is_business")), None)
    competitors = [r for r in sov_rows if not r.get("is_business")]
    leader = competitors[0] if competitors else None

    print(f"[EXEC_PLAN_FN] sov_rows={len(sov_rows)} owner={bool(owner)} competitors={len(competitors)}")
    for r in sov_rows:
        print(f"  row: {r.get('competitor_name')} reviews={r.get('reviews_total')} is_biz={r.get('is_business')} rating={r.get('google_rating')}")

    plan: list[dict] = []

    # ── Item 1: Review momentum ───────────────────────────────────────────
    if owner and leader:
        owner_reviews = int(owner.get("reviews_total") or 0)
        leader_reviews = int(leader.get("reviews_total") or 0)
        leader_name = leader.get("competitor_name") or "the market leader"
        leader_rating = leader.get("google_rating")
        owner_rating = owner.get("google_rating")

        # Check review pulse for top mover (fastest-gaining competitor)
        top_mover_name = None
        top_mover_gain = 0
        pulse_series = (sections.get("review_pulse") or {})
        # review pulse doesn't expose series directly, use competitor_deltas via SOV delta
        for r in competitors:
            delta = int(r.get("reviews_delta_7d") or r.get("share_change_7d_pct") or 0)
            if delta > top_mover_gain:
                top_mover_gain = delta
                top_mover_name = r.get("competitor_name")

        if owner_reviews >= leader_reviews:
            # Owner is leading
            next_comp = competitors[1] if len(competitors) > 1 else None
            if next_comp:
                gap_below = owner_reviews - int(next_comp.get("reviews_total") or 0)
                challenger = next_comp.get("competitor_name") or "your closest challenger"
                action = "Keep your review lead — it's your most visible competitive advantage."
                detail = (
                    f"You lead {leader_name} with {owner_reviews:,} reviews. "
                    f"{challenger} is {gap_below:,} reviews behind. "
                    f"Ask every customer for a review this month to maintain your margin."
                )
            else:
                action = "Keep your review lead — it's your most visible competitive advantage."
                detail = (
                    f"You lead the market with {owner_reviews:,} reviews. "
                    f"A consistent review ask after every appointment keeps the gap growing."
                )
        else:
            gap = leader_reviews - owner_reviews
            months_moderate = max(1, round(gap / max(gap // 12, 1)))
            per_month_needed = max(3, gap // 6)
            if top_mover_name and top_mover_gain > 0:
                mover_note = f" {top_mover_name} gained {top_mover_gain} reviews last week alone — they are accelerating."
            else:
                mover_note = ""
            action = f"Close the {gap:,}-review gap with {leader_name}."
            detail = (
                f"You have {owner_reviews:,} reviews vs. {leader_name}'s {leader_reviews:,}. "
                f"You need roughly {per_month_needed}+ new reviews per month to close this in 6 months.{mover_note} "
                f"Build a post-appointment review ask into your workflow today."
            )

        plan.append({"type": "execution_review_gap", "action": action, "detail": detail})

    # ── Item 2: Positioning / rating advantage ────────────────────────────
    # Use praise words from perception narrative + rating comparison
    perception_text = (sections.get("customer_perception_insights") or {}).get("body") or ""
    praise_words = ""
    if "words that come up most are:" in perception_text:
        try:
            praise_words = perception_text.split("words that come up most are:")[1].split(".")[0].strip()
        except Exception:
            pass

    # Find competitor with lowest rating (biggest opening)
    rated_comps = [r for r in competitors if r.get("google_rating")]
    weakest_rated = min(rated_comps, key=lambda r: float(r.get("google_rating") or 5), default=None)
    owner_rating_val = float(owner.get("google_rating") or 0) if owner else 0

    if praise_words:
        action = f"Own the words customers already use: {praise_words}."
        detail = (
            f"These are the phrases showing up in local reviews right now. "
            f"Add them to your Google Business profile description and coach customers to use them when they leave a review."
        )
        if weakest_rated and owner_rating_val > float(weakest_rated.get("google_rating") or 0):
            weak_name = weakest_rated.get("competitor_name") or "a competitor"
            weak_rating = weakest_rated.get("google_rating")
            detail += (
                f" Your {owner_rating_val:.1f}★ rating already beats {weak_name}'s {weak_rating}★ — "
                f"make sure that comparison is visible to anyone shopping around."
            )
    elif owner_rating_val > 0 and weakest_rated:
        weak_name = weakest_rated.get("competitor_name") or "a competitor"
        weak_rating = weakest_rated.get("google_rating")
        action = f"Your rating is a competitive edge — make it visible."
        detail = (
            f"At {owner_rating_val:.1f}★ you outrank {weak_name} ({weak_rating}★). "
            f"Highlight your rating on your homepage, in follow-up emails, and on your Google profile."
        )
    else:
        action = "Highlight one clear advantage customers should associate with your business."
        detail = "Reinforce one strength — comfort, convenience, or communication — consistently across your website and Google profile."

    if action:
        plan.append({"type": "execution_positioning", "action": action, "detail": detail})

    # ── Item 3: Exploit competitor weakness ───────────────────────────────
    friction_data = sections.get("customer_friction_signals") or {}
    friction_insights = friction_data.get("insights") or []

    # Find the competitor with the most/highest friction
    worst_comp = None
    worst_theme = None
    worst_count = 0
    for fi in friction_insights:
        details = fi.get("details") or {}
        cname = details.get("competitor_name") or ""
        theme = details.get("theme") or details.get("top_theme") or ""
        count = int(details.get("complaint_count") or details.get("market_total") or 0)
        if cname and count > worst_count:
            worst_count = count
            worst_comp = cname
            worst_theme = theme

    if worst_comp and worst_theme:
        action = f"Turn {worst_comp}'s weakness into your headline."
        detail = (
            f"Customers are complaining about {worst_theme.lower()} at {worst_comp} "
            f"({worst_count} mentions in recent reviews). "
            f"If that's a strength of yours, say so explicitly — on your website, in your Google profile, and when customers ask why they should choose you."
        )
    else:
        # Fallback: use the messaging gap from perception narrative if available
        perception_body = (sections.get("customer_perception_insights") or {}).get("body") or ""
        weak_spot_comp = ""
        weak_spot_detail = ""
        if "Weak Spot" in perception_body:
            try:
                # Extract "Blue Ash Dental Group's Weak Spot\nTheir patients..."
                before_label = perception_body.split("Weak Spot")[0]
                # Grab the competitor name from the last line before "Weak Spot"
                weak_spot_comp = before_label.strip().split("\n")[-1].replace("'s", "").strip()
                raw = perception_body.split("Weak Spot")[1].split("\n\n")[0].strip()
                # Replace anonymous "Their" with the competitor name
                if weak_spot_comp:
                    raw = raw.replace("Their customers", f"{weak_spot_comp}'s customers")
                    raw = raw.replace("their website", f"{weak_spot_comp}'s website")
                    raw = raw.replace("they're advertising", f"{weak_spot_comp} is advertising")
                weak_spot_detail = raw
            except Exception:
                pass

        if weak_spot_detail:
            comp_label = f" {weak_spot_comp}'s" if weak_spot_comp else " a competitor's"
            action = f"Exploit{comp_label} gap between what customers value and what they advertise."
            # Truncate cleanly at sentence boundary
            full = weak_spot_detail[:600]
            last_period = full.rfind(".")
            detail = full[:last_period + 1] if last_period > 100 else full
        else:
            action = "Improve how your reviews and credibility are presented."
            detail = "Feature top reviews prominently, respond to every review, and highlight credentials or guarantees on your homepage."

    plan.append({"type": "execution_weakness", "action": action, "detail": detail})

    return plan


# ---------------------------------------------------------------------------
# Daily snapshot collection — called by Render cron job every 24 hours
# POST /cron/collect-snapshots
# Header: x-admin-key: <ADMIN_API_KEY>
# ---------------------------------------------------------------------------
@router.post("/cron/collect-snapshots")
def cron_collect_snapshots(request: Request, background_tasks: BackgroundTasks):
    """
    Iterates over every active business and collects a fresh Google Places
    snapshot for each competitor. Runs in the background so it doesn't timeout.
    Protected by x-admin-key header matching ADMIN_API_KEY env var.
    """
    from app.core.config import settings

    admin_key = settings.ADMIN_API_KEY
    if admin_key:
        provided = request.headers.get("x-admin-key", "")
        if provided != admin_key:
            raise HTTPException(status_code=401, detail="Unauthorized")

    # Fetch all distinct business IDs that have competitors
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select distinct business_id
                from public.competitors
                where google_place_id is not null
                """
            )
            biz_rows = cur.fetchall()

    biz_ids = [row["business_id"] for row in biz_rows]

    def _run():
        for biz_id in biz_ids:
            try:
                result = collect_snapshots_for_business(biz_id)
                logger.info("snapshot ok business_id=%s inserted=%s", biz_id, result.inserted)
            except Exception as exc:
                logger.warning("cron snapshot failed for business_id=%s: %s", biz_id, exc)

    background_tasks.add_task(_run)
    return {"status": "started", "businesses": len(biz_ids)}


# ---------------------------------------------------------------------------
# Monthly scheduled report runner — called by Render cron job on the 1st
# POST /cron/run-scheduled-reports
# Header: x-admin-key: <ADMIN_API_KEY>
# ---------------------------------------------------------------------------
@router.post("/cron/run-scheduled-reports")
def cron_run_scheduled_reports(request: Request, background_tasks: BackgroundTasks):
    """
    Finds all enabled schedules whose next_run_at is due, generates and
    emails each report, then advances next_run_at to the 1st of the next month.
    Safe to run via Render cron on the 1st of each month at 8am ET.
    """
    from app.services.report_schedule_service import find_due_schedules, update_schedule_run_times

    admin_key = request.headers.get("x-admin-key") or request.headers.get("X-Admin-Key")
    if admin_key != settings.ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")

    now = datetime.now(timezone.utc)
    due = find_due_schedules(now)

    if not due:
        logger.info("cron/run-scheduled-reports: no schedules due at %s", now)
        return {"status": "ok", "due": 0, "queued": 0}

    logger.info("cron/run-scheduled-reports: %d schedule(s) due", len(due))

    def _run_one(schedule: dict):
        import re as _re
        business_id = str(schedule.get("business_id") or "")
        schedule_id = str(schedule.get("id") or "")

        if not business_id:
            return

        try:
            print(f"[CRON-REPORTS] generating report for business {business_id}", flush=True)

            # Generate full report
            report = generate_business_report(UUID(business_id))
            if hasattr(report, "model_dump"):
                report_dict = report.model_dump()
            elif hasattr(report, "dict"):
                report_dict = report.dict()
            else:
                report_dict = report if isinstance(report, dict) else {}

            report_id = str(report_dict.get("id") or "")
            if not report_id:
                logger.error("cron-reports: report generation failed for %s", business_id)
                _send_admin_alert(
                    subject=f"⚠️ Report generation failed — {business_id}",
                    body=f"Report generation returned no ID for business {business_id}.\nSchedule: {schedule_id}",
                )
                return

            # Ensure not marked as free preview
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE generated_reports SET sections = sections || '{\"is_free_preview\": false}'::jsonb WHERE id = %s",
                        (report_id,),
                    )
                conn.commit()

            # Look up contact email from business notes
            contact_email = None
            contact_name = "there"
            business_name_val = ""
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT name, notes FROM businesses WHERE id = %s", (business_id,))
                    biz = cur.fetchone()
                    if biz:
                        business_name_val = biz.get("name") or ""
                        notes = biz.get("notes") or ""
                        m = _re.search(r'<([^>]+@[^>]+)>', notes)
                        if m:
                            contact_email = m.group(1)
                        m2 = _re.search(r'Contact:\s*([^<\n]+)', notes)
                        if m2:
                            contact_name = m2.group(1).strip().split()[0]

            # Email the report
            if contact_email:
                from app.api.generated_reports import send_generated_report_email, SendReportRequest
                send_generated_report_email(
                    UUID(report_id),
                    SendReportRequest(
                        to_email=contact_email,
                        subject=f"Your Monthly Pulse LCI Report — {business_name_val}",
                        body_text=(
                            f"Hi {contact_name},\n\n"
                            "Your updated monthly competitive intelligence report is attached. "
                            "It shows how your market has shifted over the past 30 days, "
                            "where competitors are gaining ground, and your top priorities for this month.\n\n"
                            "— Pulse LCI"
                        ),
                    ),
                )
                print(f"[CRON-REPORTS] report emailed to {contact_email} for {business_id}", flush=True)
            else:
                logger.warning("cron-reports: no email found for business %s", business_id)

            # Advance next_run_at to 1st of next month
            _now = datetime.now(timezone.utc)
            if _now.month == 12:
                next_run = datetime(_now.year + 1, 1, 1, 8, 0, 0, tzinfo=timezone.utc)
            else:
                next_run = datetime(_now.year, _now.month + 1, 1, 8, 0, 0, tzinfo=timezone.utc)

            update_schedule_run_times(
                UUID(schedule_id),
                last_run_at=_now,
                next_run_at=next_run,
            )
            print(f"[CRON-REPORTS] schedule advanced to {next_run} for {business_id}", flush=True)

        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            print(f"[CRON-REPORTS] ERROR for business {business_id}: {exc}\n{tb}", flush=True)
            _send_admin_alert(
                subject=f"⚠️ Cron report crashed — {business_name_val or business_id}",
                body=f"Business: {business_name_val or business_id}\nError: {exc}\n\n{tb}",
            )

    for schedule in due:
        background_tasks.add_task(_run_one, dict(schedule))

    return {"status": "started", "due": len(due), "queued": len(due)}


# --------------------
# Reports (legacy "reports" table)
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

    # -------------------------------------------------
    # CLIENT-DATA SAFETY GUARD
    # Never allow previous insights from another business
    # to leak into the current report.
    # -------------------------------------------------

    current_sov = safe_sections.get("share_of_voice") or []

    valid_competitor_names = {
        str(row.get("competitor_name") or row.get("name") or "").strip()
        for row in current_sov
        if isinstance(row, dict)
    }

    valid_competitor_names = {
        name for name in valid_competitor_names if name
    }

    def _text_has_foreign_client_data(text: str) -> bool:
        text_lower = str(text or "").lower()

        known_wrong_terms = [
            "blue ash dental",
            "riversbend dental",
            "cedar village",
            "dental group",
        ]

        return any(term in text_lower for term in known_wrong_terms)

    def _filter_cross_client_items(items):
        cleaned = []

        for item in items or []:
            if isinstance(item, str):
                if not _text_has_foreign_client_data(item):
                    cleaned.append(item)

            elif isinstance(item, dict):
                combined = " ".join(
                    str(item.get(k) or "")
                    for k in [
                        "title",
                        "summary",
                        "action",
                        "detail",
                        "why_it_matters",
                        "how_to_implement",
                    ]
                )

                if not _text_has_foreign_client_data(combined):
                    cleaned.append(item)

        return cleaned

    safe_previous_insights = _filter_cross_client_items(safe_previous_insights)

    presentation = build_client_facing_insights(
        safe_insights,
        previous_insights=safe_previous_insights,
        sections=safe_sections,
    )

    def _dynamic_why_it_matters(item: Dict[str, Any]) -> str:
        insight_type = str(item.get("type") or "").lower()
        details = item.get("details") or {}

        if insight_type == "baseline_rank":
            owner_rank = int(details.get("owner_rank") or 0)
            owner_reviews = int(details.get("owner_reviews_total") or 0)
            market_size = int(details.get("market_size") or 0)
            if owner_rank == 1 and owner_reviews:
                return (
                    f"You hold the #1 spot across {market_size} competitors with {owner_reviews:,} reviews. "
                    "Your rank is a trust signal — customers comparing providers will see it immediately."
                )
            elif owner_rank and owner_reviews and market_size:
                return (
                    f"You're ranked #{owner_rank} of {market_size} with {owner_reviews:,} reviews. "
                    "Review rank directly influences which business customers call first."
                )

        if insight_type == "leader_gap":
            gap = int(details.get("gap_reviews") or 0)
            leader_name = details.get("leader_name") or "the market leader"
            leader_reviews = int(details.get("leader_reviews_total") or 0)
            if gap and leader_name and leader_reviews:
                months_est = max(1, round(gap / 10))
                return (
                    f"You trail {leader_name} ({leader_reviews:,} reviews) by {gap:,} reviews. "
                    f"At 10 new reviews per month, closing this gap takes about {months_est} months without acceleration."
                )

        if insight_type == "market_dominance":
            leader_share = float(details.get("leader_share_pct") or 0)
            owner_is_leader = details.get("owner_is_leader") or details.get("is_owner")
            leader_name = details.get("leader_name") or "the market leader"
            if owner_is_leader and leader_share:
                return (
                    f"You hold {leader_share}% of all market reviews. "
                    "Consistent growth is what keeps that gap from closing on you."
                )
            elif leader_share:
                return (
                    f"{leader_name} controls {leader_share}% of market reviews — "
                    "that dominance builds default trust with customers before they ever compare."
                )

        return ""

    def _dynamic_how_to_implement(item: Dict[str, Any]) -> str:
        summary = str(item.get("summary") or "").lower()
        insight_type = str(item.get("type") or "").lower()
        details = item.get("details") or {}

        if insight_type == "baseline_rank" or "ranked #" in summary or "rank #" in summary:
            owner_rank = int(details.get("owner_rank") or 0)
            owner_reviews = int(details.get("owner_reviews_total") or 0)
            market_size = int(details.get("market_size") or 0)
            if owner_rank == 1 and owner_reviews:
                return (
                    "Set a monthly target of at least 10 new reviews. "
                    "Ask customers at checkout or in a follow-up message — consistency matters more than volume spikes."
                )
            elif owner_rank and owner_reviews:
                return (
                    f"You currently hold rank #{owner_rank} with {owner_reviews:,} reviews. "
                    "Set a monthly review target and check monthly whether you're closing the gap to the rank above you."
                )
            return (
                "Use this rank as the baseline: set a monthly review target, compare movement against the next competitor, "
                "and track whether the gap is shrinking."
            )

        if insight_type == "leader_gap" or "trail the market leader" in summary:
            gap = int(details.get("gap_reviews") or 0)
            leader_name = details.get("leader_name") or "the market leader"
            if gap and leader_name:
                return (
                    f"Request reviews after every visit and set a monthly target of at least 10. "
                    f"Track your gap to {leader_name} each month — if it shrinks by 10+ reviews, your cadence is working."
                )
            return (
                "Set a review target tied to the gap, then consistently request reviews after each visit and track progress monthly."
            )

        if insight_type == "market_dominance":
            owner_is_leader = details.get("owner_is_leader") or details.get("is_owner")
            leader_name = details.get("leader_name") or "the market leader"
            if owner_is_leader:
                return (
                    "Maintain a consistent review request process — aim for at least 10 new reviews per month. "
                    "Monitor your closest challenger monthly: if they gain more than 20 reviews in a single month, accelerate your pace."
                )
            return (
                f"Pick one thing you do better than {leader_name} — speed, communication, or pricing — "
                "and make it visible in your reviews and messaging. Volume gap closes slowly; positioning gap can close fast."
            )

        if "behind" in summary or "gap" in summary:
            return (
                "Set a review target tied to the gap, then consistently request reviews after each visit and track progress monthly."
            )

        if "controls" in summary or "top 2 competitors" in summary:
            return (
                "Position directly against dominant competitors by highlighting clear differentiators, stronger trust signals, "
                "and reasons customers should choose you instead."
            )

        if "review share" in summary or "share of voice" in summary:
            return (
                "Make your review position more visible by improving your Google Business Profile, increasing review volume, "
                "and reinforcing trust signals."
            )

        if "customer language" in summary or "friendly staff" in summary or "easy scheduling" in summary:
            return (
                "Use exact customer phrases in your website, Google profile, and review responses to reinforce what customers value most."
            )

        if "friction" in insight_type or "complaint" in summary:
            return (
                "Identify the operational cause behind complaints, fix the issue, and communicate improvements through responses and messaging."
            )

        if "perception" in insight_type:
            return (
                "Turn the strongest customer perception into a clear positioning message and repeat it across all customer touchpoints."
            )

        return (
            "Identify the highest-impact opportunity from this signal and act on it this month — either by improving how you generate reviews, "
            "how you position your services, or how clearly you communicate trust and results to potential customers."
        )

    def _normalize_item(item: Dict[str, Any]) -> Dict[str, Any]:
        summary = (
            item.get("summary")
            or item.get("message")
            or item.get("implication")
            or item.get("recommended_action")
            or ""
        )

        # -----------------------------------------
        # Tighten summary phrasing (headline quality)
        # -----------------------------------------
        if summary:
            s = summary.strip()

            if "messaging does not fully reflect" in s.lower():
                summary = "Messaging is not aligned with how customers describe value."

            elif "positioning opening" in s.lower():
                summary = "Clear positioning opportunity: emphasize speed and convenience."

            elif "reducing positioning clarity" in s.lower():
                summary = summary.replace("reducing positioning clarity", "").strip().rstrip(".") + "."

        action = item.get("action") or item.get("recommended_action") or summary

        # -----------------------------------------
        # Override generic action for rank-1 leader
        # -----------------------------------------
        if str(item.get("type") or "").lower() == "baseline_rank":
            _d = item.get("details") or {}
            if int(_d.get("owner_rank") or 0) == 1:
                action = "Defend your #1 position and widen the gap over your closest competitor."

        # -----------------------------------------
        # Prevent signal-as-action (low value)
        # -----------------------------------------
        if action:
            action_lower = action.lower()

            if "share of voice increased" in action_lower:
                action = "Reinforce your market lead by increasing review visibility and strengthening trust signals."

            if "share of voice decreased" in action_lower:
                action = "Recover lost visibility by accelerating review generation and reinforcing your core positioning."

        why = (
            item.get("why_it_matters")
            or _dynamic_why_it_matters(item)
            or item.get("implication")
            or "This signal may affect local visibility, trust, or customer choice."
        )

        how = item.get("how_to_implement") or _dynamic_how_to_implement(item)

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

    # -------------------------------------------------
    # FORCE CLEAN POSITION CONTEXT (NO CROSS-CLIENT DATA)
    # -------------------------------------------------

    current_sov = safe_sections.get("share_of_voice") or []

    def _build_clean_position_context():
        rows = [
            r for r in current_sov
            if isinstance(r, dict)
        ]

        if not rows:
            return []

        rows = sorted(
            rows,
            key=lambda r: int(r.get("reviews_total") or r.get("review_count") or 0),
            reverse=True,
        )

        you = next((r for r in rows if r.get("is_business")), None)
        leader = rows[0] if rows else None

        if not you or not leader:
            return []

        you_name = str(you.get("competitor_name") or you.get("name") or "you")
        leader_name = str(leader.get("competitor_name") or leader.get("name") or "the market leader")

        you_reviews = int(you.get("reviews_total") or you.get("review_count") or 0)
        leader_reviews = int(leader.get("reviews_total") or leader.get("review_count") or 0)

        you_share = float(you.get("share_pct") or you.get("share") or 0)
        leader_share = float(leader.get("share_pct") or leader.get("share") or 0)

        context = []

        if leader.get("is_business"):
            context.append(
                f"You lead the market with {you_reviews:,} reviews and roughly {you_share:.0f}% review share."
            )

            if len(rows) > 1:
                challenger = rows[1]
                challenger_name = challenger.get("competitor_name") or challenger.get("name") or "the closest challenger"
                challenger_reviews = int(challenger.get("reviews_total") or challenger.get("review_count") or 0)

                context.append(
                    f"{challenger_name} is the closest challenger at {challenger_reviews:,} reviews, so protect the lead by keeping review generation consistent."
                )

        else:
            gap = max(leader_reviews - you_reviews, 0)

            context.append(
                f"You are chasing {leader_name}, who leads the market with {leader_reviews:,} reviews and roughly {leader_share:.0f}% review share."
            )

            context.append(
                f"You currently have {you_reviews:,} reviews, leaving a {gap:,}-review gap to close."
            )

            below_you = [
                r for r in rows
                if not r.get("is_business")
                and int(r.get("reviews_total") or r.get("review_count") or 0) < you_reviews
            ]

            if below_you:
                closest_below = below_you[0]
                below_name = closest_below.get("competitor_name") or closest_below.get("name") or "the closest challenger"
                below_reviews = int(closest_below.get("reviews_total") or closest_below.get("review_count") or 0)
                cushion = you_reviews - below_reviews

                context.append(
                    f"{below_name} is {cushion:,} reviews behind you, so protect your position while closing the gap above."
                )

        return context[:3]

    # Override any contaminated Position Context from presentation layer
    clean_position_context = _build_clean_position_context()

    summary_side = presentation.get("summary_side") or {}
    if not isinstance(summary_side, dict):
        summary_side = {}

    summary_side["position_context"] = clean_position_context

    return {
        "flat_insights": flat_insights,
        "grouped_sections": grouped_sections,
        "summary_text": presentation.get("summary_text") or "",
        "summary_side": summary_side,
        "position_context": clean_position_context,
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

            # 🔥 APPLY GLOBAL RULES HERE
            sections["report_experience"] = apply_report_integrity_rules(
                sections.get("report_experience") or {},
                sections,
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


def _detect_customer_label(business_name: str) -> str:
    """Auto-detect the right customer term from business name keywords."""
    name = business_name.lower()
    patient_keywords = [
        "dental", "dentist", "dentistry", "orthodont", "oral", "smile", "teeth",
        "medical", "clinic", "health", "chiropractic", "optometry", "vision",
        "eye care", "pediatric", "therapy", "physical therapy", "urgent care",
        "hospital", "physician", "doctor", "dermatology", "cardiology",
    ]
    client_keywords = [
        "attorney", "law firm", "lawyer", "legal", "accountant", "accounting",
        "cpa", "financial", "advisor", "consultant", "salon", "spa", "beauty",
        "counseling", "coaching",
    ]
    for kw in patient_keywords:
        if kw in name:
            return "patients"
    for kw in client_keywords:
        if kw in name:
            return "clients"
    return "customers"


@router.post("/business/{business_id}/reports/generate", response_model=GeneratedReportOut)
def generate_business_report(
    business_id: UUID,
    admin_ok: None = Depends(verify_admin_key)
):
    """
    Manual report generation:
      - inserts generated_reports row with summary_text + sections
      - sections: top_moves, insights, momentum, velocity_trends, threats, share_of_voice
      - Step 5/6 compares PREVIOUS latest report (pre-insert) vs this new report (post-insert)
    """
    try:
        days = 30

        # Refresh review text before building insights so perception/friction sections are current
        try:
            ingest_reviews_for_business(str(business_id))
        except Exception as e:
            logger.warning("review ingestion skipped during report generation: %s", e)

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

        # Determine if this is a free-preview prospect (schedule exists but is disabled)
        is_free_preview = False
        if isinstance(schedule_meta, dict) and schedule_meta.get("id"):
            try:
                schedule_id = UUID(str(schedule_meta["id"]))
            except Exception:
                schedule_id = UUID("00000000-0000-0000-0000-000000000000")
            is_free_preview = not bool(schedule_meta.get("is_enabled", True))

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
        customer_label = "customers"

        try:
            business_obj = get_business_with_competitors(business_id)

            if hasattr(business_obj, "model_dump"):
                business_obj = business_obj.model_dump()
            elif hasattr(business_obj, "dict"):
                business_obj = business_obj.dict()

            b = (business_obj or {}).get("business") or {}
            business_name = b.get("name")
            # Always auto-detect from business name — the schema default of "customers"
            # on BusinessOut would otherwise mask the detection.
            _stored_label = b.get("customer_label") or ""
            customer_label = (
                _stored_label
                if _stored_label and _stored_label != "customers"
                else _detect_customer_label(business_name or "")
            )

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

        # Build a competitor_id -> reviews_total map from SOV rows for sorting
        _sov_rows = (share_of_voice or {}).get("rows") or []
        _competitor_review_totals: dict = {}
        for _sov_row in _sov_rows:
            _cid = str(_sov_row.get("competitor_id") or "")
            _rt = int(_sov_row.get("reviews_total") or _sov_row.get("review_count") or 0)
            if _cid:
                _competitor_review_totals[_cid] = _rt

        try:
            review_insights = build_review_insights_for_business(
                business_id=str(business_id),
                owner_competitor_id=str(owner_competitor_id) if owner_competitor_id else None,
                owner_name=business_name,
                competitor_review_totals=_competitor_review_totals,
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
            "is_free_preview": is_free_preview,
        }

        if not customer_perception_text:
            customer_perception_text = (
                "Recent customer review signals show where competitors are gaining trust, "
                "where friction is appearing, and which themes should shape this month's positioning. "
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

            # Only use friction themes to build perception text if review insights
            # didn't already produce something meaningful.
            if not customer_perception_text and themes:
                # Pick the theme with the highest market_total, not the first in the list
                active_themes = [t for t in themes if isinstance(t, dict) and int(t.get("market_total") or 0) > 0]
                top_theme = max(active_themes, key=lambda t: int(t.get("market_total") or 0)) if active_themes else None

                if top_theme:
                    theme_label = top_theme.get("theme_label") or top_theme.get("theme_key")
                    leader_name = top_theme.get("leader_competitor_name")
                    customer_perception_text = (
                        f"Customer feedback in this market is primarily driven by {str(theme_label).lower()}. "
                        f"{leader_name or 'A leading competitor'} is currently the most visible competitor in this area. "
                        "Strengthening your positioning around this theme can improve conversion and reinforce competitive advantage."
                    )
                else:
                    customer_perception_text = (
                        "No dominant customer perception theme emerged this period. "
                        "This creates an opportunity to differentiate by owning a key experience area such as speed, communication, or convenience."
                    )

                sections["customer_perception_insights"]["body"] = customer_perception_text

            elif customer_perception_text:
                # Review insights produced real text — persist it into the section
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
            # Ensure perception body is always populated, even if the friction block failed
            if not sections.get("customer_perception_insights", {}).get("body"):
                sections["customer_perception_insights"]["body"] = customer_perception_text


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

        # ── Data-driven Execution Plan ─────────────────────────────────────
        # Build 3 concrete action items from real numbers rather than
        # relying on the generic presentation-layer output.
        focus = _build_data_driven_execution_plan(sections)
        print("[EXEC_PLAN] generated", len(focus), "items:", [f.get("action", "")[:60] for f in focus])

        if not focus:
            focus = sections["report_experience"].get("this_month_focus") or insights[:3]

        if not focus:
            focus = [
                {
                    "type": "fallback_focus",
                    "summary": "Maintain review momentum and watch competitor movement this month.",
                    "severity": "info",
                    "details": {},
                }
            ]

        sections["report_experience"]["this_month_focus"] = focus

        if not sections["report_experience"].get("immediate_priorities"):
            sections["report_experience"]["immediate_priorities"] = len(focus[:3]) or 1

        sections["share_of_voice_donut"] = _build_share_of_voice_donut_payload(sections)
        sections["review_count_bar"] = _build_review_count_bar_payload(sections)
        sections["review_pulse"] = _build_review_pulse_payload(business_id)
        premium_headline = build_executive_headline(sections)

        if sections.get("report_experience"):
            sections["report_experience"]["summary_text"] = premium_headline

        owner_row = next((r for r in (share_of_voice.get("rows") or []) if r.get("is_business")), None)
        sections["business_name"] = (
            (owner_row or {}).get("competitor_name")
            or (owner_row or {}).get("name")
            or "Client"
        )

        summary_text = premium_headline

        # Store customer_label in sections so the HTML template can use it
        sections["customer_label"] = customer_label
        sections["customer_label_singular"] = (
            customer_label.rstrip("s") if customer_label.endswith("s") else customer_label
        )

        # ── Customer label normalisation ────────────────────────────────────
        # Normalize all customer-term variants to the detected label.
        # Base text in insight_presentation_service uses a mix of "patients"
        # and "customers" — we replace both to the correct term for this business.
        if customer_label:
            import json as _json2

            def _replace_patient_terms(text: str, label: str) -> str:
                if not text:
                    return text
                label_pl = label          # plural  (e.g. "patients", "customers", "clients")
                label_sg = label.rstrip("s") if label.endswith("s") else label  # singular
                # Always normalize both "patients" and "customers" to the target label,
                # since base text in the codebase uses both terms inconsistently.
                # Replace plural forms first (order matters to avoid partial matches)
                text = text.replace("patients", label_pl)
                text = text.replace("Patients", label_pl.capitalize())
                text = text.replace("patient", label_sg)
                text = text.replace("Patient", label_sg.capitalize())
                text = text.replace("customers", label_pl)
                text = text.replace("Customers", label_pl.capitalize())
                text = text.replace("customer", label_sg)
                text = text.replace("Customer", label_sg.capitalize())
                return text

            def _walk_and_replace(obj, label):
                if isinstance(obj, str):
                    return _replace_patient_terms(obj, label)
                if isinstance(obj, dict):
                    return {k: _walk_and_replace(v, label) for k, v in obj.items()}
                if isinstance(obj, list):
                    return [_walk_and_replace(i, label) for i in obj]
                return obj

            sections = _walk_and_replace(sections, customer_label)
            summary_text = _replace_patient_terms(summary_text or "", customer_label)

        print("\n=== REPORT DEBUG ===")
        print("business_name:", business_name)
        print("customer_label:", customer_label)
        perception_body = (sections.get("customer_perception_insights") or {}).get("body") or ""
        print("perception_body_preview:", perception_body[:120])
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

            def _apply_label_to_experience(exp: dict) -> dict:
                """Apply customer label replacement to a report_experience dict."""
                if not customer_label or not isinstance(exp, dict):
                    return exp
                _lpl = customer_label
                _lsg = customer_label.rstrip("s") if customer_label.endswith("s") else customer_label
                def _rt(text):
                    if not isinstance(text, str):
                        return text
                    text = text.replace("patients", _lpl).replace("Patients", _lpl.capitalize())
                    text = text.replace("patient", _lsg).replace("Patient", _lsg.capitalize())
                    text = text.replace("customers", _lpl).replace("Customers", _lpl.capitalize())
                    text = text.replace("customer", _lsg).replace("Customer", _lsg.capitalize())
                    return text
                def _walk(obj):
                    if isinstance(obj, str): return _rt(obj)
                    if isinstance(obj, dict): return {k: _walk(v) for k, v in obj.items()}
                    if isinstance(obj, list): return [_walk(i) for i in obj]
                    return obj
                return _walk(exp)

            if pc:
                created.setdefault("sections", {}).setdefault("insights", []).append(pc)
                created["sections"]["report_experience"] = _apply_label_to_experience(
                    _build_report_experience_payload(
                        created["sections"].get("insights"),
                        previous_insights=previous_insights,
                        sections=created.get("sections") or {},
                    )
                )
                _append_insight_to_report_in_db(
                    created_id,
                    pc,
                    previous_insights=previous_insights,
                )

            if mm:
                created.setdefault("sections", {}).setdefault("insights", []).append(mm)
                created["sections"]["report_experience"] = _apply_label_to_experience(
                    _build_report_experience_payload(
                        created["sections"].get("insights"),
                        previous_insights=previous_insights,
                        sections=created.get("sections") or {},
                    )
                )
                _append_insight_to_report_in_db(
                    created_id,
                    mm,
                    previous_insights=previous_insights,
                )

        sections = created.get("sections") or {}

        if not sections.get("report_experience"):
            sections["report_experience"] = {}

        # ── Re-apply data-driven execution plan after Step 5/6 rebuilds report_experience ──
        final_focus = _build_data_driven_execution_plan(sections)
        if not final_focus:
            final_focus = sections["report_experience"].get("this_month_focus") or []
        if not final_focus:
            final_focus = [{
                "type": "fallback_focus",
                "summary": "Set a clear monthly review-growth target, improve your positioning against top competitors, and track whether you are closing the gap.",
                "priority": "Immediate"
            }]

        # Apply customer label normalization to the execution plan before saving to DB
        _cl = sections.get("customer_label") or "customers"
        _cl_sg = _cl.rstrip("s") if _cl.endswith("s") else _cl
        def _fix_term(text: str) -> str:
            if not text:
                return text
            text = text.replace("patients", _cl).replace("Patients", _cl.capitalize())
            text = text.replace("patient", _cl_sg).replace("Patient", _cl_sg.capitalize())
            text = text.replace("customers", _cl).replace("Customers", _cl.capitalize())
            text = text.replace("customer", _cl_sg).replace("Customer", _cl_sg.capitalize())
            return text
        def _fix_item(item):
            if not isinstance(item, dict):
                return item
            return {k: _fix_term(v) if isinstance(v, str) else v for k, v in item.items()}
        final_focus = [_fix_item(f) for f in final_focus]

        # Patch just this_month_focus in the DB — read current saved sections,
        # update only the execution plan, write back. Avoids overwriting the
        # good report_experience cards built by _append_insight_to_report_in_db.
        try:
            import json as _json
            with get_conn() as _conn:
                with _conn.cursor() as _cur:
                    _cur.execute(
                        "SELECT sections FROM generated_reports WHERE id = %s",
                        (str(created_id),),
                    )
                    _saved = _cur.fetchone()

                if _saved:
                    _saved_sections = _saved["sections"]
                    if isinstance(_saved_sections, str):
                        _saved_sections = _json.loads(_saved_sections)
                    if isinstance(_saved_sections, dict):
                        if "report_experience" not in _saved_sections:
                            _saved_sections["report_experience"] = {}
                        _saved_sections["report_experience"]["this_month_focus"] = final_focus[:3]
                        _saved_sections["report_experience"]["immediate_priorities"] = len(final_focus[:3]) or 1
                        with get_conn() as _conn2:
                            with _conn2.cursor() as _cur2:
                                _cur2.execute(
                                    "UPDATE generated_reports SET sections = %s WHERE id = %s",
                                    (_json.dumps(_saved_sections), str(created_id)),
                                )
                            _conn2.commit()
                        created["sections"] = _saved_sections
                        sections = _saved_sections
        except Exception as _e:
            logger.warning("failed to persist final execution plan to DB: %s", _e)

        return created

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate report: {str(e)}")