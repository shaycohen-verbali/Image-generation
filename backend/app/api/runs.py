from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import db_dependency
from app.schemas import (
    AssetOut,
    PromptOut,
    RetryRunResponse,
    RunDetailOut,
    RunOut,
    RunsCreateRequest,
    ScoreOut,
    StageResultOut,
)
from app.services.repository import Repository

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


def _json_dict(value: str) -> dict:
    try:
        result = json.loads(value)
        return result if isinstance(result, dict) else {}
    except json.JSONDecodeError:
        return {}


@router.post("", response_model=list[RunOut])
def create_runs(payload: RunsCreateRequest, db: Session = Depends(db_dependency)) -> list[RunOut]:
    repo = Repository(db)
    config = repo.get_runtime_config()

    runs = repo.create_runs(
        payload.entry_ids,
        quality_threshold=payload.quality_threshold or config.quality_threshold,
        max_optimization_attempts=payload.max_optimization_attempts
        if payload.max_optimization_attempts is not None
        else config.max_optimization_loops,
    )

    return [
        RunOut(
            id=run.id,
            entry_id=run.entry_id,
            status=run.status,
            current_stage=run.current_stage,
            quality_score=run.quality_score,
            quality_threshold=run.quality_threshold,
            optimization_attempt=run.optimization_attempt,
            max_optimization_attempts=run.max_optimization_attempts,
            technical_retry_count=run.technical_retry_count,
            error_detail=run.error_detail,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )
        for run in runs
    ]


@router.get("", response_model=list[RunOut])
def list_runs(
    status: str | None = Query(default=None),
    entry_id: str | None = Query(default=None),
    min_score: float | None = Query(default=None),
    max_score: float | None = Query(default=None),
    db: Session = Depends(db_dependency),
) -> list[RunOut]:
    repo = Repository(db)
    runs = repo.list_runs(status=status, entry_id=entry_id, min_score=min_score, max_score=max_score)
    return [
        RunOut(
            id=run.id,
            entry_id=run.entry_id,
            status=run.status,
            current_stage=run.current_stage,
            quality_score=run.quality_score,
            quality_threshold=run.quality_threshold,
            optimization_attempt=run.optimization_attempt,
            max_optimization_attempts=run.max_optimization_attempts,
            technical_retry_count=run.technical_retry_count,
            error_detail=run.error_detail,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )
        for run in runs
    ]


@router.get("/{run_id}", response_model=RunDetailOut)
def get_run(run_id: str, db: Session = Depends(db_dependency)) -> RunDetailOut:
    repo = Repository(db)
    run, stages, prompts, assets, scores = repo.run_details(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    run_payload = RunOut(
        id=run.id,
        entry_id=run.entry_id,
        status=run.status,
        current_stage=run.current_stage,
        quality_score=run.quality_score,
        quality_threshold=run.quality_threshold,
        optimization_attempt=run.optimization_attempt,
        max_optimization_attempts=run.max_optimization_attempts,
        technical_retry_count=run.technical_retry_count,
        error_detail=run.error_detail,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )

    return RunDetailOut(
        run=run_payload,
        stages=[
            StageResultOut(
                id=stage.id,
                stage_name=stage.stage_name,
                attempt=stage.attempt,
                status=stage.status,
                request_json=_json_dict(stage.request_json),
                response_json=_json_dict(stage.response_json),
                error_detail=stage.error_detail,
                created_at=stage.created_at,
            )
            for stage in stages
        ],
        prompts=[
            PromptOut(
                id=prompt.id,
                stage_name=prompt.stage_name,
                attempt=prompt.attempt,
                prompt_text=prompt.prompt_text,
                needs_person=prompt.needs_person,
                source=prompt.source,
                raw_response_json=_json_dict(prompt.raw_response_json),
                created_at=prompt.created_at,
            )
            for prompt in prompts
        ],
        assets=[
            AssetOut(
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
            for asset in assets
        ],
        scores=[
            ScoreOut(
                id=score.id,
                stage_name=score.stage_name,
                attempt=score.attempt,
                score_0_100=score.score_0_100,
                pass_fail=score.pass_fail,
                rubric_json=_json_dict(score.rubric_json),
                created_at=score.created_at,
            )
            for score in scores
        ],
    )


@router.post("/{run_id}/retry", response_model=RetryRunResponse)
def retry_run(run_id: str, db: Session = Depends(db_dependency)) -> RetryRunResponse:
    repo = Repository(db)
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    run = repo.retry_run_from_last_failure(run)
    return RetryRunResponse(run_id=run.id, status=run.status, retry_from_stage=run.retry_from_stage)
