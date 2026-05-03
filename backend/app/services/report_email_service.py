from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

from app.services.report_pdf_service import get_latest_generated_report


def send_report_pdf_email(
    *,
    business_id: str,
    pdf_path: str,
    to_email: str,
) -> None:
    email_from = os.getenv("LCI_EMAIL_FROM")
    email_password = os.getenv("LCI_EMAIL_PASSWORD")

    if not email_from or not email_password:
        raise RuntimeError("Set LCI_EMAIL_FROM and LCI_EMAIL_PASSWORD first")

    report = get_latest_generated_report(business_id)
    if not report:
        raise RuntimeError("No generated report found")

    subject = report.get("title") or "Competitive Report"
    summary = report.get("summary_text") or "Attached is your latest competitive report."

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = to_email
    msg.set_content(
        f"""Attached is the latest LCI PDF report.

{summary}
"""
    )

    pdf_file = Path(pdf_path)
    if not pdf_file.exists():
        raise RuntimeError(f"PDF not found: {pdf_path}")

    with open(pdf_file, "rb") as f:
        msg.add_attachment(
            f.read(),
            maintype="application",
            subtype="pdf",
            filename=pdf_file.name,
        )

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(email_from, email_password)
        server.send_message(msg)