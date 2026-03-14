from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import db_dependency
from app.schemas import AssetOut
from app.services.repository import Repository
from app.services.storage import materialize_path

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
        created_at=asset.created_at,
    )


@router.get("/{asset_id}/content")
def get_asset_content(asset_id: str, db: Session = Depends(db_dependency)) -> FileResponse:
    repo = Repository(db)
    asset = repo.get_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")

    path = materialize_path(asset.abs_path, cache_namespace="assets")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Asset file missing")

    return FileResponse(
        path,
        media_type=asset.mime_type or "application/octet-stream",
        filename=asset.file_name,
    )
