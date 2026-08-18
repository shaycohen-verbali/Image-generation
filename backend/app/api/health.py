from __future__ import annotations

from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import db_dependency

router = APIRouter(tags=["health"])


@router.get("/livez", include_in_schema=False)
def livez() -> dict[str, str]:
    """Process-level health check that never waits on the database."""
    return {"status": "ok"}


@router.get("/healthz")
def healthz(db: Session = Depends(db_dependency)) -> dict[str, str | int | float]:
    started_at = perf_counter()
    try:
        # Render probes this endpoint repeatedly.  Keep readiness to one
        # trivial round-trip instead of counting the entire runs table.
        db.execute(text("SELECT 1")).scalar_one()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail={
                "status": "degraded",
                "database_status": "unavailable",
                "database_latency_ms": round((perf_counter() - started_at) * 1000, 3),
                "error_category": type(exc).__name__,
            },
        ) from None
    return {
        "status": "ok",
        "database_status": "ok",
        "database_latency_ms": round((perf_counter() - started_at) * 1000, 3),
    }
