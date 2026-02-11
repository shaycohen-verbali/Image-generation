from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import db_dependency
from app.schemas import ExportCreateRequest, ExportOut
from app.services.export_service import ExportService
from app.services.repository import Repository
from app.services.storage import exports_root

router = APIRouter(prefix="/api/v1/exports", tags=["exports"])


def _json_dict(value: str) -> dict:
    try:
        result = json.loads(value)
        return result if isinstance(result, dict) else {}
    except json.JSONDecodeError:
        return {}


def _resolve_export_file(record, *, preferred_path: str, fallback_name: str) -> str:
    export_dir = (exports_root() / record.id).resolve()
    candidate = Path(preferred_path).resolve() if preferred_path else (export_dir / fallback_name).resolve()
    if not candidate.is_relative_to(export_dir):
        return ""
    if not candidate.exists():
        return ""
    return candidate.as_posix()


def _to_export_out(record) -> ExportOut:
    csv_path = _resolve_export_file(record, preferred_path=record.csv_path, fallback_name="export.csv")
    white_bg_zip_path = _resolve_export_file(record, preferred_path=record.zip_path, fallback_name="images_white_bg.zip")
    with_bg_zip_path = _resolve_export_file(
        record,
        preferred_path="",
        fallback_name="images_with_bg_last_attempt.zip",
    )
    manifest_path = _resolve_export_file(record, preferred_path=record.manifest_path, fallback_name="manifest.json")

    return ExportOut(
        id=record.id,
        status=record.status,
        filter_json=_json_dict(record.filter_json),
        csv_path=csv_path,
        zip_path=white_bg_zip_path,
        with_bg_zip_path=with_bg_zip_path,
        manifest_path=manifest_path,
        csv_download_url=f"/api/v1/exports/{record.id}/download/csv",
        white_bg_zip_download_url=f"/api/v1/exports/{record.id}/download/white-bg-zip",
        with_bg_zip_download_url=f"/api/v1/exports/{record.id}/download/with-bg-zip",
        manifest_download_url=f"/api/v1/exports/{record.id}/download/manifest",
        error_detail=record.error_detail,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@router.post("", response_model=ExportOut)
def create_export(payload: ExportCreateRequest, db: Session = Depends(db_dependency)) -> ExportOut:
    service = ExportService(db)
    record = service.create_export(payload.model_dump(exclude_none=True))
    if record is None:
        raise HTTPException(status_code=500, detail="Failed to create export")
    return _to_export_out(record)


@router.get("/{export_id}", response_model=ExportOut)
def get_export(export_id: str, db: Session = Depends(db_dependency)) -> ExportOut:
    repo = Repository(db)
    record = repo.get_export(export_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Export not found")
    return _to_export_out(record)


@router.get("/{export_id}/download/{artifact}")
def download_export_artifact(artifact: str, export_id: str, db: Session = Depends(db_dependency)) -> FileResponse:
    repo = Repository(db)
    record = repo.get_export(export_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Export not found")

    artifact_map = {
        "csv": ("export.csv", record.csv_path),
        "white-bg-zip": ("images_white_bg.zip", record.zip_path),
        "with-bg-zip": ("images_with_bg_last_attempt.zip", ""),
        "manifest": ("manifest.json", record.manifest_path),
    }
    if artifact not in artifact_map:
        raise HTTPException(status_code=404, detail="Artifact not found")

    fallback_name, preferred_path = artifact_map[artifact]
    resolved = _resolve_export_file(record, preferred_path=preferred_path, fallback_name=fallback_name)
    if not resolved:
        raise HTTPException(status_code=404, detail="Artifact file not found")

    return FileResponse(
        resolved,
        filename=Path(resolved).name,
        media_type="application/octet-stream",
    )
