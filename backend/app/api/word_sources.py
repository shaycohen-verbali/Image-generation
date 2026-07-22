from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import db_dependency
from app.schemas import (
    CsvJobImportResponse,
    CsvJobExportResponse,
    WordSourceExportRequest,
    WordSourceImportRequest,
    WordSourceOut,
    WordSourceRowsOut,
)
from app.services.csv_dag_service import CsvDagService
from app.services.word_sources import WordSourceService

router = APIRouter(prefix="/api/v1/word-sources", tags=["word-sources"])


@router.get("", response_model=list[WordSourceOut])
def list_word_sources() -> list[WordSourceOut]:
    return [WordSourceOut(**source) for source in WordSourceService().list_sources()]


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


@router.post("/{table_name}/export", response_model=CsvJobExportResponse)
def export_word_source_rows(
    table_name: str,
    payload: WordSourceExportRequest,
    db: Session = Depends(db_dependency),
) -> CsvJobExportResponse:
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

    # A lightweight local job gives the existing exporter its usual manifest and
    # download lifecycle. Passing the selected inventory rows directly preserves
    # the source table unchanged while packaging its current paths and prompts.
    service = CsvDagService(db)
    job_rows = [
        {
            "word": row.get("word", ""),
            "part_of_sentence": row.get("part_of_sentence", ""),
            "category": row.get("category", ""),
            "context": row.get("context", ""),
            "sense_id": row.get("sense_id", ""),
            "_word_source_table": row.get("_word_source_table", table_name),
            "_word_source_row_id": row.get("_word_source_row_id", ""),
            "_word_source_word": row.get("_word_source_word", ""),
            "_word_source_part_of_speech": row.get("_word_source_part_of_speech", ""),
            "_word_source_sense_id": row.get("_word_source_sense_id", ""),
            "_word_source_existing_paths": row.get("_word_source_existing_paths", {}),
        }
        for row in rows
    ]
    imported = service.import_word_source_rows(
        table_name=table_name,
        rows=job_rows,
        person_gender_options=[],
        person_age_options=[],
        person_skin_color_options=[],
    )
    result = service.export_job(
        imported["job_id"],
        export_fields=payload.export_fields,
        inventory_rows_override=rows,
    )
    return CsvJobExportResponse(
        **result,
        download_url=f"/api/v1/csv-jobs/{imported['job_id']}/export/download",
    )
