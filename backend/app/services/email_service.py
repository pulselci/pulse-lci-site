from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Optional
from uuid import UUID

from app.core.db import get_conn
from app.core.config import settings


@dataclass
class EmailSendResult:
    ok: bool
    error: str | None = None


def _log_report_delivery(
    report_id: UUID | str | None,
    recipient_email: str,
    status: str,
    error: str | None = None,
) -> None:
    """
    Best-effort delivery log.
    Never raises back into email sending.
    """
    if not report_id:
        return

    sql = """
    insert into report_delivery_logs (
        report_id,
        recipient_email,
        status,
        error,
        sent_at
    )
    values (
        %s,
        %s,
        %s,
        %s,
        %s
    )
    """

    sent_at = datetime.now(timezone.utc) if status == "sent" else None

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        str(report_id),
                        recipient_email,
                        status,
                        error,
                        sent_at,
                    ),
                )
            conn.commit()
    except Exception as log_error:
        print(f"[report_delivery_logs] failed to write log: {log_error}")


def send_report_email(
    to_email: str,
    subject: str,
    body_text: str,
    pdf_bytes: bytes,
    filename: str | None = None,
    report_id: UUID | str | None = None,
    business_name: Optional[str] = None,
    summary_text: Optional[str] = None,
) -> EmailSendResult:
    """
    Sends an email with a PDF attachment via SMTP.

    Env vars used:
      SMTP_HOST (default: smtp.gmail.com)
      SMTP_PORT (default: 587)
      SMTP_USER (required for real send)
      SMTP_PASS (required for real send)
      SMTP_FROM (optional; defaults to SMTP_USER)
      SMTP_TLS  (default: true)
      EMAIL_DRY_RUN (default: false)
    """
    dry_run = os.getenv("EMAIL_DRY_RUN", "false").strip().lower() in ("1", "true", "yes")

    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "587"))

    user = (settings.SMTP_USER or "").strip()
    password = (settings.SMTP_PASS or "").strip()
    from_email = os.getenv("SMTP_FROM", "").strip() or user

    use_tls = os.getenv("SMTP_TLS", "true").strip().lower() in ("1", "true", "yes")

    if not to_email:
        return EmailSendResult(ok=False, error="to_email is required")

    if dry_run:
        print(f"[EMAIL_DRY_RUN] Would send to={to_email} subject={subject} bytes={len(pdf_bytes)}")
        _log_report_delivery(
            report_id=report_id,
            recipient_email=to_email,
            status="sent",
            error=None,
        )
        return EmailSendResult(ok=True)

    if not user or not password:
        result = EmailSendResult(ok=False, error="Missing SMTP_USER or SMTP_PASS")
        _log_report_delivery(
            report_id=report_id,
            recipient_email=to_email,
            status="failed",
            error=result.error,
        )
        return result

    if not from_email:
        result = EmailSendResult(ok=False, error="Missing SMTP_FROM or SMTP_USER")
        _log_report_delivery(
            report_id=report_id,
            recipient_email=to_email,
            status="failed",
            error=result.error,
        )
        return result

    display_business_name = business_name or "your business"

    headline = (summary_text or "").strip()
    if len(headline) > 220:
        headline = headline[:217] + "..."

    html_body = f"""
    <html>
    <body style="margin: 0; padding: 0; background: #f4f7fb; font-family: Arial, Helvetica, sans-serif; color: #172033;">
        <div style="max-width: 680px; margin: 0 auto; padding: 28px 20px;">
        <div style="background: #ffffff; border: 1px solid #dce6f5; border-radius: 14px; padding: 28px;">
            <div style="font-size: 11px; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase; color: #5c6f91; margin-bottom: 10px;">
            Pulse LCI
            </div>

            <h2 style="margin: 0 0 10px 0; font-size: 24px; line-height: 1.2; color: #122033;">
            Your latest competitive intelligence report is ready
            </h2>

            {f'''
            <div style="margin: 0 0 16px 0; padding: 14px 16px; background: #f8fbff; border: 1px solid #dce6f5; border-radius: 10px;">
            <div style="font-size: 11px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: #62738f; margin-bottom: 6px;">
                Headline takeaway
            </div>
            <div style="font-size: 15px; line-height: 1.5; font-weight: 700; color: #122033;">
                {headline}
            </div>
            </div>
            ''' if headline else ''}

            <p style="margin: 0 0 14px 0; font-size: 14px; line-height: 1.6; color: #30415f;">
            Attached is your latest Pulse LCI report for <strong>{business_name or "your business"}</strong>.
            </p>

            <p style="margin: 0 0 18px 0; font-size: 14px; line-height: 1.6; color: #30415f;">
            This report highlights competitive movement in your local market, review momentum, positioning gaps, and the clearest next actions to take.
            </p>

            <p style="margin: 0; font-size: 14px; line-height: 1.6; color: #30415f;">
            Thanks,<br>
            <strong>Pulse LCI Reports</strong>
            </p>
        </div>
        </div>
    </body>
    </html>
    """

    msg = EmailMessage()
    msg["From"] = f"Pulse LCI Reports <{from_email}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body_text or "")
    msg.add_alternative(html_body, subtype="html")

    msg.add_attachment(
        pdf_bytes,
        maintype="application",
        subtype="pdf",
        filename=filename or "LCI_Report.pdf",
    )

    try:
        print(f"[EMAIL DEBUG] host={host} port={port} user={user} from={from_email} to={to_email} dry_run={dry_run} tls={use_tls}")
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.ehlo()
            if use_tls:
                smtp.starttls()
                smtp.ehlo()
            smtp.login(user, password)
            smtp.send_message(msg)

        _log_report_delivery(
            report_id=report_id,
            recipient_email=to_email,
            status="sent",
            error=None,
        )
        return EmailSendResult(ok=True)

    except Exception as e:
        error_text = str(e)
        _log_report_delivery(
            report_id=report_id,
            recipient_email=to_email,
            status="failed",
            error=error_text,
        )
        return EmailSendResult(ok=False, error=error_text)

def send_plain_email(
    to_email: str,
    subject: str,
    body: str,
) -> "EmailSendResult":
    """
    Send a plain-text email with no PDF attachment.
    Uses the same SMTP credentials as send_report_email.
    """
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    user = settings.SMTP_USER
    password = settings.SMTP_PASS
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "587"))
    from_email = os.getenv("SMTP_FROM") or user
    use_tls = os.getenv("SMTP_TLS", "true").strip().lower() in ("1", "true", "yes")
    dry_run = os.getenv("EMAIL_DRY_RUN", "false").strip().lower() in ("1", "true", "yes")

    if dry_run:
        logger.info(f"[EMAIL DRY RUN] plain email to={to_email} subject={subject}")
        return EmailSendResult(ok=True, error=None)

    if not user or not password:
        return EmailSendResult(ok=False, error="Missing SMTP_USER or SMTP_PASS")

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_email
        msg["To"] = to_email
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(host, port) as server:
            if use_tls:
                server.starttls()
            server.login(user, password)
            server.sendmail(from_email, [to_email], msg.as_string())

        return EmailSendResult(ok=True, error=None)
    except Exception as e:
        logger.warning(f"[EMAIL] plain email failed to={to_email}: {e}")
        return EmailSendResult(ok=False, error=str(e))