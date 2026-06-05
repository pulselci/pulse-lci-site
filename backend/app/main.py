from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.generated_reports import router as generated_reports_router
from app.api.intake import router as intake_router
from app.api.outreach import router as outreach_router
from app.api.routes import router as api_router
from app.core.db import close_pool

app = FastAPI(
    title="Local Competitor Intelligence (Phase 1)",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # allow all for now (safe for MVP)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Existing API routes
app.include_router(api_router)

# Generated report PDF routes
app.include_router(generated_reports_router)

# Prospect intake (free report request form)
app.include_router(intake_router)

# Outreach approval queue
app.include_router(outreach_router)


@app.get("/admin/onboarding", include_in_schema=False)
def onboarding_form():
    html_path = Path(__file__).resolve().parent / "static" / "onboarding.html"
    return FileResponse(html_path)


@app.get("/outreach/ui", include_in_schema=False)
def outreach_queue_ui():
    html_path = Path(__file__).resolve().parent / "static" / "outreach_queue.html"
    return FileResponse(html_path)


@app.get("/logo.png", include_in_schema=False)
def serve_logo():
    logo_path = Path(__file__).resolve().parent / "static" / "pulse-lci-logo.png"
    return FileResponse(logo_path, media_type="image/png")


@app.on_event("shutdown")
def on_shutdown():
    close_pool()