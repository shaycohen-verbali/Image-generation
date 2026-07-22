from __future__ import annotations

from time import perf_counter

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_dependency
from app.services.repository import Repository

router = APIRouter(tags=["health"])


@router.get("/livez", include_in_schema=False)
def livez() -> dict[str, str]:
    """Process-level health check that never waits on the database."""
    return {"status": "ok"}


@router.get("/healthz")
def healthz(db: Session = Depends(db_dependency)) -> dict[str, str | int | float]:
    started_at = perf_counter()
    repo = Repository(db)
    runs = repo.count_runs()
    return {
        "status": "ok",
        "database_status": "ok",
        "database_latency_ms": round((perf_counter() - started_at) * 1000, 3),
        "runs": runs,
    }
