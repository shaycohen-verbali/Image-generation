from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import db_dependency
from app.schemas import AssetOut
from app.services.repository import Repository

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
