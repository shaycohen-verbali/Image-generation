from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import db_dependency
from app.schemas import ExportCreateRequest, ExportOut
from app.services.export_service import ExportService
from app.services.repository import Repository

router = APIRouter(prefix="/api/v1/exports", tags=["exports"])


def _json_dict(value: str) -> dict:
    try:
        result = json.loads(value)
        return result if isinstance(result, dict) else {}
    except json.JSONDecodeError:
        return {}


@router.post("", response_model=ExportOut)
def create_export(payload: ExportCreateRequest, db: Session = Depends(db_dependency)) -> ExportOut:
    service = ExportService(db)
    record = service.create_export(payload.model_dump(exclude_none=True))
    if record is None:
        raise HTTPException(status_code=500, detail="Failed to create export")

    return ExportOut(
        id=record.id,
        status=record.status,
        filter_json=_json_dict(record.filter_json),
        csv_path=record.csv_path,
        zip_path=record.zip_path,
        manifest_path=record.manifest_path,
        error_detail=record.error_detail,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@router.get("/{export_id}", response_model=ExportOut)
def get_export(export_id: str, db: Session = Depends(db_dependency)) -> ExportOut:
    repo = Repository(db)
    record = repo.get_export(export_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Export not found")

    return ExportOut(
        id=record.id,
        status=record.status,
        filter_json=_json_dict(record.filter_json),
        csv_path=record.csv_path,
        zip_path=record.zip_path,
        manifest_path=record.manifest_path,
        error_detail=record.error_detail,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
