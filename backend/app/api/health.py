from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_dependency
from app.services.repository import Repository

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz(db: Session = Depends(db_dependency)) -> dict[str, str | int]:
    repo = Repository(db)
    return {"status": "ok", "runs": repo.count_runs()}
