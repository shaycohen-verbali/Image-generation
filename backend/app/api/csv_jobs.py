from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import db_dependency
from app.schemas import (
    CsvJobCancelResponse,
    CsvJobClearResponse,
    CsvJobExportResponse,
    CsvJobImportResponse,
    CsvJobInventorySyncResponse,
    CsvJobOut,
    CsvJobOverviewOut,
    CsvJobRetryResponse,
    CsvJobStartResponse,
    ExecutionMode,
)
from app.services.csv_dag_service import CsvDagService
from app.services.person_profiles import DEFAULT_AGE, DEFAULT_GENDER, DEFAULT_SKIN_COLOR
from app.services.storage import materialize_path

router = APIRouter(prefix="/api/v1/csv-jobs", tags=["csv-jobs"])


def _parse_list_field(value: str | None, default: list[str]) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return list(default)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON list payload: {exc}") from exc
    if not isinstance(parsed, list):
        raise HTTPException(status_code=400, detail="Expected a JSON list")
    return [str(item or "").strip().lower() for item in parsed if str(item or "").strip()]


@router.post("/import", response_model=CsvJobImportResponse)
def import_csv_job(
    file: UploadFile = File(...),
    execution_mode: ExecutionMode = Form(default="csv_dag"),
    person_gender_options: str = Form(default='["male"]'),
    person_age_options: str = Form(default='["kid"]'),
    person_skin_color_options: str = Form(default='["white"]'),
    db: Session = Depends(db_dependency),
) -> CsvJobImportResponse:
    if execution_mode != "csv_dag":
        raise HTTPException(status_code=400, detail="Use /entries/import-csv for legacy CSV import")
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are supported")

    service = CsvDagService(db)
    result = service.import_csv_job(
        file_name=file.filename,
        content=file.file.read(),
        execution_mode=execution_mode,
        person_gender_options=_parse_list_field(person_gender_options, [DEFAULT_GENDER]),
        person_age_options=_parse_list_field(person_age_options, [DEFAULT_AGE]),
        person_skin_color_options=_parse_list_field(person_skin_color_options, [DEFAULT_SKIN_COLOR]),
    )
    return CsvJobImportResponse(**result)


@router.get("", response_model=list[CsvJobOut])
def list_csv_jobs(db: Session = Depends(db_dependency)) -> list[CsvJobOut]:
    service = CsvDagService(db)
    return [CsvJobOut(**row) for row in service.list_jobs()]


@router.delete("", response_model=CsvJobClearResponse)
def clear_csv_jobs(db: Session = Depends(db_dependency)) -> CsvJobClearResponse:
    service = CsvDagService(db)
    result = service.clear_terminal_jobs()
    return CsvJobClearResponse(**result)


@router.get("/{job_id}", response_model=CsvJobOut)
def get_csv_job(job_id: str, db: Session = Depends(db_dependency)) -> CsvJobOut:
    service = CsvDagService(db)
    job = service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="CSV job not found")
    return CsvJobOut(**job)


@router.get("/{job_id}/overview", response_model=CsvJobOverviewOut)
def get_csv_job_overview(job_id: str, db: Session = Depends(db_dependency)) -> CsvJobOverviewOut:
    service = CsvDagService(db)
    overview = service.job_overview(job_id)
    if overview is None:
        raise HTTPException(status_code=404, detail="CSV job not found")
    return CsvJobOverviewOut(**overview)


@router.post("/{job_id}/start", response_model=CsvJobStartResponse)
def start_csv_job(job_id: str, db: Session = Depends(db_dependency)) -> CsvJobStartResponse:
    service = CsvDagService(db)
    try:
        job = service.start_job(job_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return CsvJobStartResponse(job_id=job.id, status=job.status)


@router.post("/{job_id}/retry-failures", response_model=CsvJobRetryResponse)
def retry_csv_job_failures(job_id: str, db: Session = Depends(db_dependency)) -> CsvJobRetryResponse:
    service = CsvDagService(db)
    try:
        job, count = service.retry_failures(job_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return CsvJobRetryResponse(job_id=job.id, requeued_task_count=count, status=job.status)


@router.post("/{job_id}/cancel", response_model=CsvJobCancelResponse)
def cancel_csv_job(job_id: str, db: Session = Depends(db_dependency)) -> CsvJobCancelResponse:
    service = CsvDagService(db)
    try:
        job, count = service.cancel_job(job_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return CsvJobCancelResponse(job_id=job.id, status=job.status, canceled_task_count=count)


@router.post("/{job_id}/export", response_model=CsvJobExportResponse)
def export_csv_job(job_id: str, db: Session = Depends(db_dependency)) -> CsvJobExportResponse:
    service = CsvDagService(db)
    try:
        result = service.export_job(job_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return CsvJobExportResponse(
        **result,
        download_url=f"/api/v1/csv-jobs/{job_id}/export/download",
    )


@router.post("/{job_id}/sync-inventory", response_model=CsvJobInventorySyncResponse)
def sync_csv_job_inventory(job_id: str, db: Session = Depends(db_dependency)) -> CsvJobInventorySyncResponse:
    service = CsvDagService(db)
    try:
        result = service.sync_inventory(job_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return CsvJobInventorySyncResponse(**result)


@router.get("/{job_id}/export/download")
def download_csv_job_export(job_id: str, db: Session = Depends(db_dependency)) -> FileResponse:
    service = CsvDagService(db)
    job_payload = service.get_job(job_id)
    if job_payload is None:
        raise HTTPException(status_code=404, detail="CSV job not found")
    repo_job = service.repo.get_csv_job(job_id)
    if repo_job is None:
        raise HTTPException(status_code=404, detail="CSV job not found")
    local_zip = service.export_local_zip_path(repo_job)
    if local_zip.exists():
        local = local_zip
    else:
        export_result = service.export_job(job_id)
        local = materialize_path(str(export_result["zip_path"]), cache_namespace="csv_job_exports")
    if not local.exists():
        raise HTTPException(status_code=404, detail="Export artifact not found")
    return FileResponse(local, media_type="application/zip", filename=service.export_zip_name(repo_job.batch_id))
