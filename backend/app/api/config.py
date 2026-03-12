from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_dependency
from app.schemas import RuntimeConfigOut, RuntimeConfigUpdate
from app.services.repository import Repository

router = APIRouter(prefix="/api/v1/config", tags=["config"])


def _to_schema(config) -> RuntimeConfigOut:
    return RuntimeConfigOut(
        quality_threshold=config.quality_threshold,
        max_optimization_loops=config.max_optimization_loops,
        max_api_retries=config.max_api_retries,
        stage_retry_limit=config.stage_retry_limit,
        worker_poll_seconds=config.worker_poll_seconds,
        max_parallel_runs=config.max_parallel_runs,
        flux_imagen_fallback_enabled=config.flux_imagen_fallback_enabled,
        openai_assistant_id=config.openai_assistant_id,
        openai_assistant_name=config.openai_assistant_name,
        prompt_engineer_mode=config.prompt_engineer_mode,
        responses_prompt_engineer_model=config.responses_prompt_engineer_model,
        responses_vector_store_id=config.responses_vector_store_id,
        visual_style_id=config.visual_style_id,
        visual_style_name=config.visual_style_name,
        visual_style_prompt_block=config.visual_style_prompt_block,
        stage1_prompt_template=config.stage1_prompt_template,
        stage3_prompt_template=config.stage3_prompt_template,
        openai_model_vision=config.openai_model_vision,
        stage3_critique_model=config.stage3_critique_model,
        stage3_generate_model=config.stage3_generate_model,
        quality_gate_model=config.quality_gate_model,
        image_aspect_ratio=config.image_aspect_ratio,
        image_resolution=config.image_resolution,
    )


@router.get("", response_model=RuntimeConfigOut)
def get_config(db: Session = Depends(db_dependency)) -> RuntimeConfigOut:
    repo = Repository(db)
    return _to_schema(repo.get_runtime_config())


@router.put("", response_model=RuntimeConfigOut)
def update_config(payload: RuntimeConfigUpdate, db: Session = Depends(db_dependency)) -> RuntimeConfigOut:
    repo = Repository(db)
    config = repo.update_runtime_config(payload.model_dump(exclude_none=True))
    return _to_schema(config)
