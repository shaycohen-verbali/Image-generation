from __future__ import annotations

import csv
import json
import uuid
import zipfile
from io import StringIO
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import db_dependency
from app.core.config import get_settings
from app.schemas import (
    CsvJobImportResponse,
    CsvJobExportResponse,
    CloudflareUploadResponse,
    WordSourceExportRequest,
    WordSourceImportRequest,
    WordSourceOut,
    WordSourceRowsOut,
)
from app.models import CloudUpload, CloudUploadBatch
from app.services.cloudflare_upload import CloudflareUploadService, configured_buckets
from app.services.csv_dag_service import CsvDagService
from app.services.matalk_export import MATALK_ARTIFACT_FILENAMES, build_matalk_tables, write_matalk_artifacts
from app.services.storage import exports_root
from app.services.utils import sanitize_filename
from app.services.word_sources import WordSourceService

router = APIRouter(prefix="/api/v1/word-sources", tags=["word-sources"])

MATALK_REMOTE_ARTIFACTS = {
    "aac_dictionary": ("dictionary", MATALK_ARTIFACT_FILENAMES["dictionary"], "text/csv"),
    "aac_image_meta": ("image_meta", MATALK_ARTIFACT_FILENAMES["image_meta"], "text/csv"),
    "aac_images": ("images", MATALK_ARTIFACT_FILENAMES["images"], "text/csv"),
    "manifest": ("manifest", MATALK_ARTIFACT_FILENAMES["manifest"], "application/json"),
}
MATALK_READY_UPLOAD_STATUSES = {"completed", "completed_with_errors"}
CLOUDFLARE_UPLOAD_REPORT_FIELDS = (
    "id", "batch_id", "source_table", "source_row_id", "word", "part_of_speech", "sense_id",
    "variant", "source_path", "original_filename", "bucket", "object_key", "destination_url", "status",
    "original_bytes", "compressed_bytes", "compression_quality", "source_sha256", "compressed_sha256",
    "error_detail", "created_at", "updated_at",
)


def _matalk_response_fields(job_id: str, result: dict) -> dict[str, object]:
    matalk = result.get("matalk") or {}
    if not matalk.get("enabled"):
        return {}
    return {
        "matalk_download_urls": {
            "aac_dictionary": f"/api/v1/csv-jobs/{job_id}/export/download/matalk/aac_dictionary",
            "aac_image_meta": f"/api/v1/csv-jobs/{job_id}/export/download/matalk/aac_image_meta",
            "aac_images": f"/api/v1/csv-jobs/{job_id}/export/download/matalk/aac_images",
            "manifest": f"/api/v1/csv-jobs/{job_id}/export/download/matalk/manifest",
        },
        "matalk_row_counts": matalk.get("row_counts") or {},
        "matalk_warnings": matalk.get("warnings") or [],
    }


@router.get("", response_model=list[WordSourceOut])
def list_word_sources() -> list[WordSourceOut]:
    return [WordSourceOut(**source) for source in WordSourceService().list_sources()]


@router.get("/cloudflare/config")
def cloudflare_config() -> dict[str, object]:
    settings = get_settings()
    return {
        "configured": bool(settings.cloudflare_r2_endpoint and settings.cloudflare_r2_access_key_id and settings.cloudflare_r2_secret_access_key),
        "buckets": configured_buckets(),
        "default_bucket": settings.cloudflare_r2_default_bucket,
        "compression_quality": settings.cloudflare_r2_compression_quality,
    }


def _prepare_cloudflare_matalk_artifacts(
    batch: CloudUploadBatch,
    db: Session,
) -> tuple[dict[str, str], dict[str, int], list[str], dict[str, Path]]:
    """Build MaTalk CSVs after R2 upload so every image URL is resolvable."""
    if not getattr(batch, "matalk_enabled", False) or batch.status not in MATALK_READY_UPLOAD_STATUSES:
        return {}, {}, [], {}
    try:
        rows = json.loads(batch.source_rows_json or "[]")
    except (TypeError, ValueError) as exc:
        return {}, {}, [f"MaTalk tables could not be prepared because the saved source rows are invalid: {exc}"], {}
    if not isinstance(rows, list):
        return {}, {}, ["MaTalk tables could not be prepared because the saved source rows are not a list."], {}

    export_dir = exports_root() / "cloudflare" / sanitize_filename(batch.id) / "matalk"
    try:
        tables = build_matalk_tables(rows, db=db, image_location="remote")
        paths = write_matalk_artifacts(export_dir, tables)
        package_path = export_dir.parent / f"{sanitize_filename(batch.id)}_csv_package.zip"
        package_manifest = {
            "package_type": "cloudflare_csv",
            "batch_id": batch.id,
            "bucket": batch.bucket,
            "image_reference_mode": "remote_url",
            "files": {
                "aac_dictionary": "matalk/aac_dictionary.csv",
                "aac_image_meta": "matalk/aac_image_meta.csv",
                "aac_images": "matalk/aac_images.csv",
                "matalk_manifest": "matalk/matalk_manifest.json",
                "matalk_readme": "matalk/matalk_README.md",
                "cloudflare_upload_report": "_metadata/cloudflare_uploads.csv",
            },
            "row_counts": tables.row_counts,
            "warnings": list(tables.warnings),
        }
        with zipfile.ZipFile(package_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(paths["dictionary"], arcname="matalk/aac_dictionary.csv")
            archive.write(paths["image_meta"], arcname="matalk/aac_image_meta.csv")
            archive.write(paths["images"], arcname="matalk/aac_images.csv")
            archive.write(paths["manifest"], arcname="matalk/matalk_manifest.json")
            archive.write(paths["readme"], arcname="matalk/matalk_README.md")
            archive.writestr("_metadata/manifest.json", json.dumps(package_manifest, ensure_ascii=False, indent=2))
            archive.writestr("_metadata/cloudflare_uploads.csv", _cloudflare_upload_report_csv(batch.id, db))
        paths["package"] = package_path
    except Exception as exc:  # noqa: BLE001 - status polling should expose a usable warning
        return {}, {}, [f"MaTalk tables could not be prepared: {exc}"], {}

    download_urls = {
        table_name: f"/api/v1/word-sources/cloud-uploads/{batch.id}/matalk/{table_name}"
        for table_name in ("aac_dictionary", "aac_image_meta", "aac_images", "manifest")
    }
    return download_urls, tables.row_counts, list(tables.warnings), paths


def _cloudflare_upload_report_csv(batch_id: str, db: Session) -> str:
    rows = db.scalars(
        select(CloudUpload).where(CloudUpload.batch_id == batch_id).order_by(CloudUpload.created_at, CloudUpload.id)
    ).all()
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=list(CLOUDFLARE_UPLOAD_REPORT_FIELDS), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        values = {field: getattr(row, field) for field in CLOUDFLARE_UPLOAD_REPORT_FIELDS}
        values["created_at"] = values["created_at"].isoformat() if values["created_at"] else ""
        values["updated_at"] = values["updated_at"].isoformat() if values["updated_at"] else ""
        writer.writerow(values)
    return output.getvalue()


@router.get("/cloud-uploads/{batch_id}/report.csv")
def cloudflare_upload_report(batch_id: str, db: Session = Depends(db_dependency)) -> Response:
    return Response(
        content=_cloudflare_upload_report_csv(batch_id, db),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="cloudflare_uploads_{batch_id}.csv"'},
    )


@router.get("/cloud-uploads/{batch_id}", response_model=CloudflareUploadResponse)
def cloudflare_upload_status(batch_id: str, db: Session = Depends(db_dependency)) -> CloudflareUploadResponse:
    batch = db.get(CloudUploadBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Cloudflare upload batch not found")
    matalk_download_urls, matalk_row_counts, matalk_warnings, paths = _prepare_cloudflare_matalk_artifacts(batch, db)
    package_path = paths.get("package")
    return CloudflareUploadResponse(
        batch_id=batch.id,
        bucket=batch.bucket,
        status=batch.status,
        row_count=batch.row_count,
        total=batch.total,
        uploaded=batch.uploaded,
        skipped=batch.skipped,
        failed=batch.failed,
        error_detail=batch.error_detail,
        report_url=f"/api/v1/word-sources/cloud-uploads/{batch.id}/report.csv",
        matalk_download_urls=matalk_download_urls,
        matalk_row_counts=matalk_row_counts,
        matalk_warnings=matalk_warnings,
        csv_package_download_url=(
            f"/api/v1/word-sources/cloud-uploads/{batch.id}/csv-package.zip"
            if package_path is not None and package_path.exists()
            else ""
        ),
    )


@router.get("/cloud-uploads/{batch_id}/matalk/{table_name}")
def cloudflare_matalk_artifact(
    batch_id: str,
    table_name: str,
    db: Session = Depends(db_dependency),
) -> FileResponse:
    batch = db.get(CloudUploadBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Cloudflare upload batch not found")
    artifact = MATALK_REMOTE_ARTIFACTS.get(str(table_name or "").strip().lower())
    if artifact is None:
        raise HTTPException(status_code=404, detail="MaTalk artifact not found")
    _, filename, media_type = artifact
    _, _, warnings, paths = _prepare_cloudflare_matalk_artifacts(batch, db)
    if warnings and not paths:
        raise HTTPException(status_code=409, detail=" ".join(warnings))
    artifact_key = artifact[0]
    local_path = paths.get(artifact_key)
    if local_path is None or not local_path.exists():
        raise HTTPException(status_code=404, detail="MaTalk artifact file not found")
    return FileResponse(local_path, media_type=media_type, filename=filename)


@router.get("/cloud-uploads/{batch_id}/csv-package.zip")
def cloudflare_csv_package(
    batch_id: str,
    db: Session = Depends(db_dependency),
) -> FileResponse:
    batch = db.get(CloudUploadBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Cloudflare upload batch not found")
    _, _, warnings, paths = _prepare_cloudflare_matalk_artifacts(batch, db)
    if warnings and "package" not in paths:
        raise HTTPException(status_code=409, detail=" ".join(warnings))
    local_path = paths.get("package")
    if local_path is None or not local_path.exists():
        raise HTTPException(status_code=404, detail="Cloudflare CSV package not found")
    return FileResponse(
        local_path,
        media_type="application/zip",
        filename=f"{sanitize_filename(batch.id)}_csv_package.zip",
    )


@router.get("/{table_name}/csv-package.zip")
def word_source_csv_package(
    table_name: str,
    selection_mode: str = Query(default="last_job", pattern="^(last_job|single|range|all)$"),
    row_id: str = Query(default="", max_length=128),
    range_start: int | None = Query(default=None, ge=1),
    range_end: int | None = Query(default=None, ge=1, le=100_000),
    db: Session = Depends(db_dependency),
) -> FileResponse:
    """Build a remote MaTalk CSV package without creating an upload batch."""
    try:
        rows = WordSourceService().get_export_rows(
            table_name,
            selection_mode=selection_mode,
            row_id=row_id,
            range_start=range_start,
            range_end=range_end,
            include_inactive=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not rows:
        raise HTTPException(status_code=404, detail="No word_inventory rows matched this selection")

    settings = get_settings()
    package_batch = CloudUploadBatch(
        id=f"pkg_{uuid.uuid4().hex[:24]}",
        bucket=settings.cloudflare_r2_default_bucket,
        source_rows_json=json.dumps(rows, default=str),
        row_count=len(rows),
        matalk_enabled=True,
        total=sum(
            1
            for row in rows
            for key, value in row.items()
            if str(key).endswith("_path") and str(value or "").strip()
        ),
        status="completed",
    )
    _, _, warnings, paths = _prepare_cloudflare_matalk_artifacts(package_batch, db)
    package_path = paths.get("package")
    if package_path is None or not package_path.exists():
        detail = " ".join(warnings) if warnings else "CSV package could not be prepared"
        raise HTTPException(status_code=409, detail=detail)
    return FileResponse(
        package_path,
        media_type="application/zip",
        filename=f"{sanitize_filename(table_name)}_{sanitize_filename(selection_mode)}_csv_package.zip",
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
    prepare_matalk_tables = payload.convert_to_matalk_tables_format or payload.destination == "cloudflare"
    try:
        rows = source_service.get_export_rows(
            table_name,
            selection_mode=payload.selection_mode,
            row_id=payload.row_id or "",
            range_start=payload.range_start,
            range_end=payload.range_end,
            include_inactive=prepare_matalk_tables,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not rows:
        raise HTTPException(status_code=404, detail="No word_inventory rows matched this selection")

    if payload.destination == "cloudflare":
        try:
            from app.core.config import get_settings
            settings = get_settings()
            bucket = payload.cloudflare_bucket or settings.cloudflare_r2_default_bucket
            batch_id = f"r2_{__import__('uuid').uuid4().hex[:24]}"
            total = sum(1 for row in rows for key, value in row.items() if str(key).endswith("_path") and str(value or "").strip())
            batch = CloudUploadBatch(
                id=batch_id,
                bucket=bucket,
                source_rows_json=json.dumps(rows, default=str),
                row_count=len(rows),
                compression_quality=payload.compression_quality,
                matalk_enabled=prepare_matalk_tables,
                total=total,
                status="queued",
            )
            db.add(batch)
            db.commit()
            result = {
                "batch_id": batch_id,
                "bucket": bucket,
                "status": "queued",
                "row_count": len(rows),
                "total": total,
                "uploaded": 0,
                "skipped": 0,
                "failed": 0,
                "report_url": f"/api/v1/word-sources/cloud-uploads/{batch_id}/report.csv",
            }
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
        convert_to_matalk_tables_format=payload.convert_to_matalk_tables_format,
    )
    return CsvJobExportResponse(
        **result,
        download_url=f"/api/v1/csv-jobs/{export_job['job_id']}/export/download",
        **_matalk_response_fields(export_job["job_id"], result),
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
