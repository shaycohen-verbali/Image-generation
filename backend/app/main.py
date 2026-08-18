from __future__ import annotations

import logging

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
from app.api.word_sources import router as word_sources_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.init_db import init_db
from app.services.storage import prune_runtime_cache

settings = get_settings()
configure_logging(settings.app_log_level)
logger = logging.getLogger(__name__)

if settings.process_role not in {"web", "all"}:
    raise RuntimeError(
        "PROCESS_ROLE=worker cannot start the HTTP API; run python -m app.worker for the worker service"
    )

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
app.include_router(word_sources_router)
app.include_router(slack_router)


@app.on_event("startup")
def on_startup() -> None:
    import time

    logger.info("api process started", extra={"process_role": settings.process_role})

    try:
        prune_runtime_cache()
    except Exception as exc:  # noqa: BLE001 - cache cleanup is best effort
        logger.warning("runtime cache prune skipped", extra={"status": type(exc).__name__})

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
