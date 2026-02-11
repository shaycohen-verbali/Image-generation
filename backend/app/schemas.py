from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class EntryCreate(BaseModel):
    word: str
    part_of_sentence: str
    category: str = ""
    context: str = ""
    boy_or_girl: str = ""
    batch: str = ""


class EntryOut(BaseModel):
    id: str
    word: str
    part_of_sentence: str
    category: str
    context: str
    boy_or_girl: str
    batch: str
    created_at: datetime
    updated_at: datetime
    latest_run_status: str | None = None
    latest_quality_score: float | None = None


class EntryImportRowResult(BaseModel):
    row_index: int
    status: str
    entry_id: str | None = None
    error: str | None = None


class EntryImportResponse(BaseModel):
    total_rows: int
    imported_count: int
    skipped_count: int
    rows: list[EntryImportRowResult]


class RunsCreateRequest(BaseModel):
    entry_ids: list[str] = Field(min_length=1)
    quality_threshold: int | None = Field(default=None, ge=95)
    max_optimization_attempts: int | None = None


class RunOut(BaseModel):
    id: str
    entry_id: str
    status: str
    current_stage: str
    quality_score: float | None
    quality_threshold: int
    optimization_attempt: int
    max_optimization_attempts: int
    technical_retry_count: int
    error_detail: str
    created_at: datetime
    updated_at: datetime


class StageResultOut(BaseModel):
    id: str
    stage_name: str
    attempt: int
    status: str
    request_json: dict[str, Any]
    response_json: dict[str, Any]
    error_detail: str
    created_at: datetime


class PromptOut(BaseModel):
    id: str
    stage_name: str
    attempt: int
    prompt_text: str
    needs_person: str
    source: str
    raw_response_json: dict[str, Any]
    created_at: datetime


class AssetOut(BaseModel):
    id: str
    run_id: str
    stage_name: str
    attempt: int
    file_name: str
    abs_path: str
    mime_type: str
    sha256: str
    width: int
    height: int
    origin_url: str
    model_name: str
    created_at: datetime


class ScoreOut(BaseModel):
    id: str
    stage_name: str
    attempt: int
    score_0_100: float
    pass_fail: bool
    rubric_json: dict[str, Any]
    created_at: datetime


class RunDetailOut(BaseModel):
    run: RunOut
    stages: list[StageResultOut]
    prompts: list[PromptOut]
    assets: list[AssetOut]
    scores: list[ScoreOut]


class RetryRunResponse(BaseModel):
    run_id: str
    status: str
    retry_from_stage: str


class ExportCreateRequest(BaseModel):
    entry_ids: list[str] | None = None
    run_ids: list[str] | None = None
    status: list[str] | None = None
    min_score: float | None = None
    max_score: float | None = None


class ExportOut(BaseModel):
    id: str
    status: str
    filter_json: dict[str, Any]
    csv_path: str
    zip_path: str
    with_bg_zip_path: str
    manifest_path: str
    csv_download_url: str
    white_bg_zip_download_url: str
    with_bg_zip_download_url: str
    manifest_download_url: str
    error_detail: str
    created_at: datetime
    updated_at: datetime


class RuntimeConfigOut(BaseModel):
    quality_threshold: int
    max_optimization_loops: int
    max_api_retries: int
    stage_retry_limit: int
    worker_poll_seconds: float
    flux_imagen_fallback_enabled: bool
    openai_assistant_id: str
    openai_assistant_name: str
    openai_model_vision: str


class RuntimeConfigUpdate(BaseModel):
    quality_threshold: int | None = Field(default=None, ge=95)
    max_optimization_loops: int | None = None
    max_api_retries: int | None = None
    stage_retry_limit: int | None = None
    worker_poll_seconds: float | None = None
    flux_imagen_fallback_enabled: bool | None = None
    openai_assistant_id: str | None = None
    openai_assistant_name: str | None = None
    openai_model_vision: str | None = None
