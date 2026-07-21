from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import db_dependency
from app.schemas import (
    CsvJobImportResponse,
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
) -> WordSourceRowsOut:
    try:
        result = WordSourceService().list_rows(
            table_name,
            search=search,
            limit=limit,
            offset=offset,
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
        rows = source_service.get_rows(table_name, payload.row_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if len(rows) != len(set(payload.row_ids)):
        raise HTTPException(status_code=404, detail="One or more selected word-source rows were not found")
    result = CsvDagService(db).import_word_source_rows(
        table_name=table_name,
        rows=rows,
        person_gender_options=payload.person_gender_options,
        person_age_options=payload.person_age_options,
        person_skin_color_options=payload.person_skin_color_options,
        override_existing_variants=payload.override_existing_variants,
    )
    return CsvJobImportResponse(**result)
