from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

from app.core.db import get_conn


def get_latest_generated_report(business_id: str) -> Dict[str, Any] | None:
    sql = """
    select id, title, summary_text, sections, generated_at
    from generated_reports
    where business_id = %s
    order by period_end desc, period_start desc, id desc
    limit 1
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (business_id,))
            row = cur.fetchone()

    return row


def _safe_insights(sections: Dict[str, Any]) -> List[Dict[str, Any]]:
    insights = (sections or {}).get("insights", [])
    return [x for x in insights if isinstance(x, dict)]


def build_report_pdf_for_business(business_id: str, output_path: str) -> str:
    report = get_latest_generated_report(business_id)
    if not report:
        raise RuntimeError("No generated report found")

    title = report.get("title") or "Competitive Report"
    summary_text = report.get("summary_text") or ""
    generated_at = str(report.get("generated_at") or "")
    sections = report.get("sections") or {}
    insights = _safe_insights(sections)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(out),
        pagesize=LETTER,
        rightMargin=0.7 * inch,
        leftMargin=0.7 * inch,
        topMargin=0.7 * inch,
        bottomMargin=0.7 * inch,
    )

    styles = getSampleStyleSheet()
    title_style = styles["Title"]
    body_style = styles["BodyText"]

    section_style = ParagraphStyle(
        "SectionHeader",
        parent=styles["Heading2"],
        fontSize=13,
        leading=16,
        textColor=colors.HexColor("#1f4e79"),
        spaceAfter=8,
        spaceBefore=10,
    )

    card_title_style = ParagraphStyle(
        "CardTitle",
        parent=styles["Heading3"],
        fontSize=10,
        leading=12,
        textColor=colors.HexColor("#1f1f1f"),
        spaceAfter=4,
    )

    small_style = ParagraphStyle(
        "Small",
        parent=body_style,
        fontSize=9,
        leading=12,
    )

    story = []
    story.append(Paragraph(title, title_style))
    story.append(Spacer(1, 0.12 * inch))
    story.append(Paragraph(f"<b>Generated:</b> {generated_at}", small_style))
    story.append(Spacer(1, 0.08 * inch))
    story.append(Paragraph(summary_text, body_style))
    story.append(Spacer(1, 0.2 * inch))

    story.append(Paragraph("Key Insights", section_style))

    if not insights:
        story.append(Paragraph("No insights found in latest report.", body_style))
    else:
        for insight in insights:
            insight_type = str(insight.get("type") or "insight").replace("_", " ").title()
            summary = insight.get("summary") or ""
            severity = insight.get("severity") or "info"

            block = [
                [Paragraph(f"<b>{insight_type}</b>  -  {severity}", card_title_style)],
                [Paragraph(summary, body_style)],
            ]

            t = Table(block, colWidths=[6.8 * inch])
            t.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eaf2f8")),
                        ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#c7d5e0")),
                        ("INNERGRID", (0, 0), (-1, -1), 0.0, colors.white),
                        ("LEFTPADDING", (0, 0), (-1, -1), 10),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                        ("TOPPADDING", (0, 0), (-1, -1), 8),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ]
                )
            )
            story.append(t)
            story.append(Spacer(1, 0.12 * inch))

    doc.build(story)
    return str(out)