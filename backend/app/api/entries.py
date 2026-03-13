from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import db_dependency
from app.schemas import (
    EntryCreate,
    EntryImportResponse,
    EntryImportRowResult,
    EntryOut,
    EntryProfileOptionsUpdate,
    EntryProfileOptionsUpdateResponse,
)
from app.services.csv_service import parse_entries_csv, validate_entry_row
from app.services.person_profiles import entry_age_options, entry_gender_options, entry_skin_color_options
from app.services.repository import Repository

router = APIRouter(prefix="/api/v1/entries", tags=["entries"])


def _generated_batch_id() -> str:
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    return f"csv_{stamp}_{uuid4().hex[:6]}"


@router.post("", response_model=EntryOut)
def create_entry(payload: EntryCreate, db: Session = Depends(db_dependency)) -> EntryOut:
    repo = Repository(db)
    entry = repo.create_entry(payload.model_dump())
    return EntryOut(
        id=entry.id,
        word=entry.word,
        part_of_sentence=entry.part_of_sentence,
        category=entry.category,
        context=entry.context,
        boy_or_girl=entry.boy_or_girl,
        person_gender_options=entry_gender_options(entry),
        person_age_options=entry_age_options(entry),
        person_skin_color_options=entry_skin_color_options(entry),
        batch=entry.batch,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


@router.post("/import-csv", response_model=EntryImportResponse)
def import_csv(file: UploadFile = File(...), db: Session = Depends(db_dependency)) -> EntryImportResponse:
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are supported")

    content = file.file.read()
    rows = parse_entries_csv(content)
    repo = Repository(db)
    generated_batch_id = _generated_batch_id() if rows else ""
    assigned_generated_batch = False

    results: list[EntryImportRowResult] = []
    imported_count = 0
    skipped_count = 0

    for index, row in enumerate(rows, start=1):
        error = validate_entry_row(row)
        if error:
            skipped_count += 1
            results.append(EntryImportRowResult(row_index=index, status="invalid", error=error))
            continue

        payload = {**row}
        if not str(row.get("batch") or "").strip() and generated_batch_id:
            assigned_generated_batch = True
        payload["batch"] = str(row.get("batch") or generated_batch_id).strip()
        entry = repo.create_entry(payload)
        imported_count += 1
        results.append(EntryImportRowResult(row_index=index, status="imported", entry_id=entry.id))

    return EntryImportResponse(
        total_rows=len(rows),
        imported_count=imported_count,
        skipped_count=skipped_count,
        batch_id=generated_batch_id if imported_count > 0 and assigned_generated_batch else "",
        rows=results,
    )


@router.get("", response_model=list[EntryOut])
def list_entries(
    word: str | None = Query(default=None),
    part_of_sentence: str | None = Query(default=None),
    category: str | None = Query(default=None),
    batch: str | None = Query(default=None),
    status: str | None = Query(default=None),
    min_score: float | None = Query(default=None),
    max_score: float | None = Query(default=None),
    db: Session = Depends(db_dependency),
) -> list[EntryOut]:
    repo = Repository(db)
    rows = repo.list_entries(
        word=word,
        part_of_sentence=part_of_sentence,
        category=category,
        batch=batch,
        status=status,
        min_score=min_score,
        max_score=max_score,
    )

    return [
        EntryOut(
            id=entry.id,
            word=entry.word,
            part_of_sentence=entry.part_of_sentence,
            category=entry.category,
            context=entry.context,
            boy_or_girl=entry.boy_or_girl,
            person_gender_options=entry_gender_options(entry),
            person_age_options=entry_age_options(entry),
            person_skin_color_options=entry_skin_color_options(entry),
            batch=entry.batch,
            created_at=entry.created_at,
            updated_at=entry.updated_at,
            latest_run_status=run.status if run else None,
            latest_quality_score=run.quality_score if run else None,
        )
        for entry, run in rows
    ]


@router.put("/apply-profile-options", response_model=EntryProfileOptionsUpdateResponse)
def apply_profile_options(payload: EntryProfileOptionsUpdate, db: Session = Depends(db_dependency)) -> EntryProfileOptionsUpdateResponse:
    repo = Repository(db)
    updated = repo.update_entries_profile_options(
        entry_ids=payload.entry_ids,
        person_gender_options=payload.person_gender_options,
        person_age_options=payload.person_age_options,
        person_skin_color_options=payload.person_skin_color_options,
    )
    return EntryProfileOptionsUpdateResponse(updated_entry_count=updated)
