from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router as api_router
from app.core.db import close_pool

app = FastAPI(
    title="Local Competitor Intelligence (Phase 1)",
    version="0.1.0",
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

app.include_router(api_router)


@app.on_event("shutdown")
def on_shutdown():
    close_pool()
