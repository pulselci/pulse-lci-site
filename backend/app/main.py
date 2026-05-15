from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.api.generated_reports import router as generated_reports_router
from app.api.routes import router as api_router
from app.core.db import close_pool
from fastapi.middleware.cors import CORSMiddleware

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

# Allow the Vite dev server(s) to call the API from the browser
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Existing API routes
app.include_router(api_router)

# Generated report PDF routes
app.include_router(generated_reports_router)


@app.get("/admin/onboarding", include_in_schema=False)
def onboarding_form():
    html_path = Path(__file__).resolve().parent / "static" / "onboarding.html"
    return FileResponse(html_path)


@app.on_event("shutdown")
def on_shutdown():
    close_pool()