from __future__ import annotations

import csv
from io import StringIO

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import db_dependency
from app.schemas import (
    CsvJobImportResponse,
    CsvJobExportResponse,
    CloudflareUploadResponse,
    WordSourceExportRequest,
    WordSourceImportRequest,
    WordSourceOut,
    WordSourceRowsOut,
)
from app.models import CloudUpload
from app.services.cloudflare_upload import CloudflareUploadService, configured_buckets
from app.services.csv_dag_service import CsvDagService
from app.services.word_sources import WordSourceService

router = APIRouter(prefix="/api/v1/word-sources", tags=["word-sources"])


@router.get("", response_model=list[WordSourceOut])
def list_word_sources() -> list[WordSourceOut]:
    return [WordSourceOut(**source) for source in WordSourceService().list_sources()]


@router.get("/cloudflare/config")
def cloudflare_config() -> dict[str, object]:
    from app.core.config import get_settings

    settings = get_settings()
    return {
        "configured": bool(settings.cloudflare_r2_endpoint and settings.cloudflare_r2_access_key_id and settings.cloudflare_r2_secret_access_key),
        "buckets": configured_buckets(),
        "default_bucket": settings.cloudflare_r2_default_bucket,
        "compression_quality": settings.cloudflare_r2_compression_quality,
    }


@router.get("/cloud-uploads/{batch_id}/report.csv")
def cloudflare_upload_report(batch_id: str, db: Session = Depends(db_dependency)) -> Response:
    rows = db.scalars(select(CloudUpload).where(CloudUpload.batch_id == batch_id).order_by(CloudUpload.created_at, CloudUpload.id)).all()
    fields = [
        "id", "batch_id", "source_table", "source_row_id", "word", "part_of_speech", "sense_id",
        "variant", "source_path", "original_filename", "bucket", "object_key", "status",
        "original_bytes", "compressed_bytes", "compression_quality", "source_sha256", "compressed_sha256",
        "error_detail", "created_at", "updated_at",
    ]
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        values = {field: getattr(row, field) for field in fields}
        values["created_at"] = values["created_at"].isoformat() if values["created_at"] else ""
        values["updated_at"] = values["updated_at"].isoformat() if values["updated_at"] else ""
        writer.writerow(values)
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="cloudflare_uploads_{batch_id}.csv"'},
    )


@router.get("/{table_name}/rows", response_model=WordSourceRowsOut)
def list_word_source_rows(
    table_name: str,
    search: str = Query(default="", max_length=256),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    selection_mode: str = Query(default="all", pattern="^(single|range|all)$"),
    row_id: str = Query(default="", max_length=128),
    range_start: int | None = Query(default=None, ge=1),
    range_end: int | None = Query(default=None, ge=1, le=100_000),
    parts_of_speech: list[str] = Query(default=[]),
) -> WordSourceRowsOut:
    try:
        result = WordSourceService().list_rows(
            table_name,
            search=search,
            limit=limit,
            offset=offset,
            selection_mode=selection_mode,
            row_id=row_id,
            range_start=range_start,
            range_end=range_end,
            parts_of_speech=parts_of_speech,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return WordSourceRowsOut(**result)


@router.post("/{table_name}/import", response_model=CsvJobImportResponse)
def import_word_source_rows(
    table_name: str,
    payload: WordSourceImportRequest,
    db: Session = Depends(db_dependency),
) -> CsvJobImportResponse:
    source_service = WordSourceService()
    try:
        rows = source_service.get_rows(
            table_name,
            selection_mode=payload.selection_mode,
            row_id=payload.row_id or "",
            range_start=payload.range_start,
            range_end=payload.range_end,
            parts_of_speech=payload.parts_of_speech,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not rows:
        raise HTTPException(status_code=404, detail="No word-source rows matched this selection")
    result = CsvDagService(db).import_word_source_rows(
        table_name=table_name,
        rows=rows,
        person_gender_options=payload.person_gender_options,
        person_age_options=payload.person_age_options,
        person_skin_color_options=payload.person_skin_color_options,
        override_existing_variants=payload.override_existing_variants,
    )
    return CsvJobImportResponse(**result)


@router.post("/{table_name}/export", response_model=CsvJobExportResponse | CloudflareUploadResponse)
def export_word_source_rows(
    table_name: str,
    payload: WordSourceExportRequest,
    db: Session = Depends(db_dependency),
) -> CsvJobExportResponse | CloudflareUploadResponse:
    source_service = WordSourceService()
    try:
        rows = source_service.get_export_rows(
            table_name,
            selection_mode=payload.selection_mode,
            row_id=payload.row_id or "",
            range_start=payload.range_start,
            range_end=payload.range_end,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not rows:
        raise HTTPException(status_code=404, detail="No word_inventory rows matched this selection")

    if payload.destination == "cloudflare":
        try:
            result = CloudflareUploadService(db).upload_rows(
                rows,
                bucket=payload.cloudflare_bucket or "",
                quality=payload.compression_quality,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return CloudflareUploadResponse(**result)

    # An export-only job gives the existing exporter its usual manifest and
    # download lifecycle without creating runnable DAG tasks. Passing the selected
    # inventory rows directly preserves the source table unchanged.
    service = CsvDagService(db)
    export_job = service.create_word_source_export_job(table_name=table_name)
    result = service.export_job(
        export_job["job_id"],
        export_fields=payload.export_fields,
        inventory_rows_override=rows,
    )
    return CsvJobExportResponse(
        **result,
        download_url=f"/api/v1/csv-jobs/{export_job['job_id']}/export/download",
    )


@router.get("/{table_name}/report.csv")
def word_source_report(
    table_name: str,
    selection_mode: str = Query(default="all", pattern="^(last_job|single|range|all)$"),
    row_id: str = Query(default="", max_length=128),
    range_start: int | None = Query(default=None, ge=1),
    range_end: int | None = Query(default=None, ge=1, le=100_000),
) -> Response:
    try:
        rows = WordSourceService().get_export_rows(
            table_name,
            selection_mode=selection_mode,
            row_id=row_id,
            range_start=range_start,
            range_end=range_end,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not rows:
        raise HTTPException(status_code=404, detail="No word-source rows matched this selection")
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key.startswith("_") or key in fields:
                continue
            fields.append(key)
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _csv_value(row.get(key)) for key in fields})
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{table_name}_{selection_mode}_report.csv"'},
    )


def _csv_value(value: object) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (dict, list, tuple)):
        import json
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)
