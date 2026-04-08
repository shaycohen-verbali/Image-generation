from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.assets import router as assets_router
from app.api.config import router as config_router
from app.api.csv_jobs import router as csv_jobs_router
from app.api.entries import router as entries_router
from app.api.exports import router as exports_router
from app.api.health import router as health_router
from app.api.runs import router as runs_router
from app.api.slack import router as slack_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.init_db import init_db

settings = get_settings()
configure_logging(settings.app_log_level)

app = FastAPI(title="AAC Image Generator and Optimizer", version="v1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(entries_router)
app.include_router(runs_router)
app.include_router(assets_router)
app.include_router(exports_router)
app.include_router(config_router)
app.include_router(csv_jobs_router)
app.include_router(slack_router)


@app.on_event("startup")
def on_startup() -> None:
    import time

    last_exc: Exception | None = None
    for attempt in range(1, 4):
        try:
            init_db()
            return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < 3:
                time.sleep(3.0 * attempt)
    raise RuntimeError("init_db failed after 3 attempts") from last_exc
