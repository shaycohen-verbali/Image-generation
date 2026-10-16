from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import db_dependency
from app.schemas import AssetOut
from app.services.repository import Repository
from app.services.storage import materialize_verified_asset

router = APIRouter(prefix="/api/v1/assets", tags=["assets"])


@router.get("/{asset_id}", response_model=AssetOut)
def get_asset(asset_id: str, db: Session = Depends(db_dependency)) -> AssetOut:
    repo = Repository(db)
    asset = repo.get_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")

    return AssetOut(
        id=asset.id,
        run_id=asset.run_id,
        stage_name=asset.stage_name,
        attempt=asset.attempt,
        file_name=asset.file_name,
        abs_path=asset.abs_path,
        mime_type=asset.mime_type,
        sha256=asset.sha256,
        width=asset.width,
        height=asset.height,
        origin_url=asset.origin_url,
        model_name=asset.model_name,
        generation_prompt_id=asset.generation_prompt_id,
        source_asset_id=asset.source_asset_id,
        canonical_path=asset.canonical_path,
        created_at=asset.created_at,
    )


@router.get("/{asset_id}/content")
def get_asset_content(asset_id: str, db: Session = Depends(db_dependency)) -> FileResponse:
    repo = Repository(db)
    asset = repo.get_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")

    try:
        path = materialize_verified_asset(asset)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail="Asset content failed integrity verification") from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="Asset file missing")

    return FileResponse(
        path,
        media_type=asset.mime_type or "application/octet-stream",
        filename=asset.file_name,
        content_disposition_type="inline",
    )
