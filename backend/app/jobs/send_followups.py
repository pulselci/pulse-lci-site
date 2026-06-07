"""
Follow-up email sequences — runs daily via /cron/send-followups.

Two sequences:

1. POST-COLD-EMAIL (outreach_prospects table)
   Day-5  — gentle nudge if no reply
   Day-12 — market insight angle

2. POST-FREE-REPORT (report_delivery_logs + businesses)
   Day-5  — "did you get a chance to look?"
   Day-12 — specific market data point + soft pitch
   Day-21 — direct subscription pitch

Sends via OUTREACH_SMTP (craig@pulselci.com) so replies go to Craig,
not the automated reports@ inbox.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from app.core.db import get_conn
from app.services.email_service import send_plain_email

logger = logging.getLogger(__name__)

PRICING_URL = "https://pulselci.com/#pricing"
FREE_REPORT_URL = "https://pulselci.com/#free-report"


# ── helpers ──────────────────────────────────────────────────────────────────

def _parse_contact_name(notes: str) -> str:
    """Extract contact name from business notes field."""
    m = re.search(r"Contact:\s*([^<\n]+?)(?:\s*<|$)", notes or "")
    return m.group(1).strip().split()[0] if m else "there"


def _send(to_email: str, subject: str, body: str) -> bool:
    result = send_plain_email(to_email=to_email, subject=subject, body=body)
    if not result.ok:
        logger.warning("Follow-up send failed to %s: %s", to_email, result.error)
    return result.ok


# ── cold email follow-ups ─────────────────────────────────────────────────────

def run_cold_email_followups() -> dict:
    """
    Day-5 and Day-12 follow-ups for outreach_prospects with status='sent'.
    Uses a ±1 day window to avoid missing sends due to cron timing.
    """
    sent1 = sent2 = 0

    with get_conn() as conn:
        with conn.cursor() as cur:

            # Day-5 follow-ups
            cur.execute("""
                SELECT id, business_name, contact_email, city, state,
                       draft_subject, top_competitor_name
                FROM outreach_prospects
                WHERE status = 'sent'
                  AND followup1_sent_at IS NULL
                  AND sent_at IS NOT NULL
                  AND sent_at >= NOW() - INTERVAL '7 days'
                  AND sent_at <= NOW() - INTERVAL '4 days'
                  AND contact_email IS NOT NULL
            """)
            day5_prospects = cur.fetchall()

            for p in day5_prospects:
                market = f"{p['city']}, {p['state']}" if p.get('city') else "your market"
                body = (
                    f"Hi,\n\n"
                    f"Just making sure this didn't get buried — happy to run the free "
                    f"competitive snapshot for {p['business_name']} whenever it works for you.\n\n"
                    f"It shows exactly where you stand against local competitors in {market} "
                    f"and what to focus on this month. Takes us about 20 minutes to pull together.\n\n"
                    f"{FREE_REPORT_URL}\n\n"
                    f"Craig\n"
                    f"Pulse LCI"
                )
                subject = f"Re: {p['draft_subject'] or 'competitive snapshot for ' + p['business_name']}"
                if _send(p['contact_email'], subject, body):
                    cur.execute(
                        "UPDATE outreach_prospects SET followup1_sent_at = NOW() WHERE id = %s",
                        (p['id'],)
                    )
                    sent1 += 1

            # Day-12 follow-ups
            cur.execute("""
                SELECT id, business_name, contact_email, city, state,
                       top_competitor_name, reviews_count, rating
                FROM outreach_prospects
                WHERE status = 'sent'
                  AND followup2_sent_at IS NULL
                  AND sent_at IS NOT NULL
                  AND sent_at >= NOW() - INTERVAL '14 days'
                  AND sent_at <= NOW() - INTERVAL '11 days'
                  AND contact_email IS NOT NULL
            """)
            day12_prospects = cur.fetchall()

            for p in day12_prospects:
                market = f"{p['city']}, {p['state']}" if p.get('city') else "your market"
                competitor_line = (
                    f"{p['top_competitor_name']} has been building review momentum recently."
                    if p.get('top_competitor_name')
                    else f"competitors in {market} have been gaining review ground recently."
                )
                body = (
                    f"Hi,\n\n"
                    f"One thing I noticed while tracking {market} — {competitor_line}\n\n"
                    f"If you'd like to see where {p['business_name']} stands in comparison, "
                    f"I can pull a free competitive snapshot this week. No strings attached.\n\n"
                    f"{FREE_REPORT_URL}\n\n"
                    f"Craig\n"
                    f"Pulse LCI"
                )
                subject = f"One thing I noticed in {market}'s market"
                if _send(p['contact_email'], subject, body):
                    cur.execute(
                        "UPDATE outreach_prospects SET followup2_sent_at = NOW() WHERE id = %s",
                        (p['id'],)
                    )
                    sent2 += 1

        conn.commit()

    logger.info("Cold email follow-ups: Day-5=%d Day-12=%d", sent1, sent2)
    return {"cold_day5": sent1, "cold_day12": sent2}


# ── post-free-report follow-ups ───────────────────────────────────────────────

def run_report_followups() -> dict:
    """
    Day-5, Day-12, and Day-21 follow-ups for businesses that received a
    free report but haven't subscribed yet.
    Skips any business where report_schedules.is_enabled = true (already subscribed).
    Tracks sends in prospect_followup_log to prevent duplicates.
    """
    counts = {5: 0, 12: 0, 21: 0}

    windows = [
        (5,  "4 days",  "7 days"),
        (12, "11 days", "14 days"),
        (21, "20 days", "24 days"),
    ]

    with get_conn() as conn:
        with conn.cursor() as cur:
            for day, min_age, max_age in windows:
                cur.execute(f"""
                    SELECT DISTINCT
                        rdl.report_id,
                        b.id   AS business_id,
                        b.name AS business_name,
                        b.notes,
                        rdl.recipient_email,
                        rdl.sent_at
                    FROM report_delivery_logs rdl
                    JOIN generated_reports gr ON gr.id = rdl.report_id
                    JOIN businesses b ON b.id = gr.business_id
                    WHERE rdl.status = 'sent'
                      AND rdl.sent_at >= NOW() - INTERVAL '{max_age}'
                      AND rdl.sent_at <= NOW() - INTERVAL '{min_age}'
                      AND rdl.recipient_email IS NOT NULL
                      -- not yet a paying subscriber
                      AND NOT EXISTS (
                          SELECT 1 FROM report_schedules rs
                          WHERE rs.business_id = b.id AND rs.is_enabled = true
                      )
                      -- follow-up not already sent for this day
                      AND NOT EXISTS (
                          SELECT 1 FROM prospect_followup_log pfl
                          WHERE pfl.business_id = b.id AND pfl.day = {day}
                      )
                """)
                rows = cur.fetchall()

                for row in rows:
                    name = _parse_contact_name(row['notes'] or "")
                    business = row['business_name']
                    email = row['recipient_email']

                    ok = False
                    if day == 5:
                        ok = _send(email, *_report_followup_day5(name, business))
                    elif day == 12:
                        ok = _send(email, *_report_followup_day12(name, business))
                    elif day == 21:
                        ok = _send(email, *_report_followup_day21(name, business))

                    if ok:
                        cur.execute(
                            """
                            INSERT INTO prospect_followup_log (business_id, day, to_email)
                            VALUES (%s, %s, %s)
                            ON CONFLICT (business_id, day) DO NOTHING
                            """,
                            (str(row['business_id']), day, email),
                        )
                        counts[day] += 1

        conn.commit()

    logger.info(
        "Report follow-ups: Day-5=%d Day-12=%d Day-21=%d",
        counts[5], counts[12], counts[21],
    )
    return {"report_day5": counts[5], "report_day12": counts[12], "report_day21": counts[21]}


# ── email templates ───────────────────────────────────────────────────────────

def _report_followup_day5(name: str, business: str) -> tuple[str, str]:
    subject = f"Did you get a chance to review your report, {name}?"
    body = (
        f"Hi {name},\n\n"
        f"Just checking in — did you get a chance to look at the competitive report "
        f"for {business}?\n\n"
        f"The friction signals section in particular is worth a look — it shows exactly "
        f"which complaint themes are showing up across your market and how your competitors "
        f"compare.\n\n"
        f"Happy to answer any questions — just reply to this email.\n\n"
        f"Craig\n"
        f"Pulse LCI"
    )
    return subject, body


def _report_followup_day12(name: str, business: str) -> tuple[str, str]:
    subject = f"One thing worth knowing about your market, {name}"
    body = (
        f"Hi {name},\n\n"
        f"Wanted to follow up on the competitive report for {business}.\n\n"
        f"Your market moves every month — review counts shift, complaint patterns change, "
        f"and competitors gain or lose ground. The snapshot you received shows where things "
        f"stood when we ran it, but that picture is already getting older.\n\n"
        f"For $99/month, you'd get this updated every month — tracking exactly how your "
        f"competitive position is shifting and what to focus on. Cancel anytime, no contracts.\n\n"
        f"{PRICING_URL}\n\n"
        f"Worth trying for one month?\n\n"
        f"Craig\n"
        f"Pulse LCI"
    )
    return subject, body


def _report_followup_day21(name: str, business: str) -> tuple[str, str]:
    subject = f"Last check-in — {business}"
    body = (
        f"Hi {name},\n\n"
        f"Last follow-up on this.\n\n"
        f"The report we sent gives you a baseline. What it can't show you is what's "
        f"changing — which competitor is quietly gaining ground, which complaint themes "
        f"are rising in your market, and whether the gap is widening or closing.\n\n"
        f"That's what the monthly subscription does. $99/month. Report delivered to your "
        f"inbox the first of every month. Cancel anytime.\n\n"
        f"{PRICING_URL}\n\n"
        f"If the timing isn't right, no worries — but if you'd like to keep watching "
        f"your market, that's the link.\n\n"
        f"Craig\n"
        f"Pulse LCI"
    )
    return subject, body


# ── main entry ────────────────────────────────────────────────────────────────

def run_all_followups() -> dict:
    cold = run_cold_email_followups()
    report = run_report_followups()
    return {**cold, **report}
