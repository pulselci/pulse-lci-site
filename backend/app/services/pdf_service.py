from __future__ import annotations

import asyncio
import sys
import os
import tempfile
import traceback
from pathlib import Path
from typing import Any, Dict

from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"


def _to_template_dict(report: Any) -> Dict[str, Any]:
    if isinstance(report, dict):
        return report

    if hasattr(report, "model_dump"):
        try:
            return report.model_dump()
        except Exception:
            pass

    if hasattr(report, "dict"):
        try:
            return report.dict()
        except Exception:
            pass

    data: Dict[str, Any] = {}
    for attr in (
        "id",
        "business_id",
        "schedule_id",
        "period_start",
        "period_end",
        "generated_at",
        "status",
        "title",
        "summary_text",
        "sections",
        "inputs",
        "error",
    ):
        try:
            data[attr] = getattr(report, attr, None)
        except Exception:
            data[attr] = None

    return data


def render_report_pdf(report: Any) -> bytes:
    report_dict = _to_template_dict(report)

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )

    html = ""
    debug_dir = Path(tempfile.gettempdir()) / "pulse_lci_pdf_debug"
    debug_dir.mkdir(parents=True, exist_ok=True)

    try:
        template = env.get_template("generated_report.html")
        html = template.render(report=report_dict)

        html_debug_path = debug_dir / "last_rendered_report.html"
        html_debug_path.write_text(html, encoding="utf-8")

    except Exception as e:
        tb = traceback.format_exc()
        raise RuntimeError(
            f"pdf_render_failed during template render: {type(e).__name__}: {e}\n{tb}"
        ) from e

    try:
        if sys.platform.startswith("win"):
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ],
            )

            try:
                page = browser.new_page()
                page.set_content(html, wait_until="load")

                pdf_bytes = page.pdf(
                    format="Letter",
                    print_background=True,
                    margin={
                        "top": "0.55in",
                        "right": "0.55in",
                        "bottom": "0.65in",
                        "left": "0.55in",
                    },
                )

                pdf_debug_path = debug_dir / "last_rendered_report.pdf"
                pdf_debug_path.write_bytes(pdf_bytes)

                return pdf_bytes

            finally:
                browser.close()

    except NotImplementedError as e:
        tb = traceback.format_exc()
        raise RuntimeError(
            "pdf_render_failed during Playwright PDF stage: "
            f"{type(e).__name__}: {e}\n"
            f"Rendered HTML saved to: {html_debug_path}\n"
            f"{tb}"
        ) from e
    except PlaywrightError as e:
        tb = traceback.format_exc()
        raise RuntimeError(
            "pdf_render_failed during Playwright browser/page stage: "
            f"{type(e).__name__}: {e}\n"
            f"Rendered HTML saved to: {html_debug_path}\n"
            f"{tb}"
        ) from e
    except Exception as e:
        tb = traceback.format_exc()
        raise RuntimeError(
            "pdf_render_failed during unknown PDF stage: "
            f"{type(e).__name__}: {e}\n"
            f"Rendered HTML saved to: {html_debug_path}\n"
            f"{tb}"
        ) from e