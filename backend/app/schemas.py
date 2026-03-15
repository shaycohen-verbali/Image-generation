from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

PromptEngineerModel = Literal["gpt-4o-mini", "gpt-4.1-mini", "gpt-5.4", "gemini-3-flash", "gemini-3-pro"]
ImageAspectRatio = Literal["1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9", "21:9"]
ImageResolution = Literal["1K", "2K", "4K"]
ImageFormat = Literal["image/png", "image/jpeg", "image/webp"]
NanoBananaSafetyLevel = Literal["default", "off", "block_none", "block_only_high", "block_medium_and_above", "block_low_and_above"]
ExecutionMode = Literal["legacy", "csv_dag"]


class EntryCreate(BaseModel):
    word: str
    part_of_sentence: str
    category: str = ""
    context: str = ""
    boy_or_girl: str = ""
    person_gender_options: list[str] = Field(default_factory=lambda: ["male"])
    person_age_options: list[str] = Field(default_factory=lambda: ["kid"])
    person_skin_color_options: list[str] = Field(default_factory=lambda: ["white"])
    batch: str = ""


class EntryOut(BaseModel):
    id: str
    word: str
    part_of_sentence: str
    category: str
    context: str
    boy_or_girl: str
    person_gender_options: list[str] = Field(default_factory=list)
    person_age_options: list[str] = Field(default_factory=list)
    person_skin_color_options: list[str] = Field(default_factory=list)
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
    batch_id: str = ""
    rows: list[EntryImportRowResult]


class EntryProfileOptionsUpdate(BaseModel):
    entry_ids: list[str] = Field(min_length=1)
    person_gender_options: list[str] = Field(default_factory=lambda: ["male"])
    person_age_options: list[str] = Field(default_factory=lambda: ["kid"])
    person_skin_color_options: list[str] = Field(default_factory=lambda: ["white"])


class EntryProfileOptionsUpdateResponse(BaseModel):
    updated_entry_count: int


class RunsCreateRequest(BaseModel):
    entry_ids: list[str] = Field(min_length=1)
    quality_threshold: int | None = Field(default=None, ge=95)
    max_optimization_attempts: int | None = None


class BatchJobSummaryOut(BaseModel):
    batch_id: str
    status: str
    run_count: int
    completed_run_count: int
    terminal_run_count: int
    passed_run_count: int = 0
    below_threshold_run_count: int = 0
    failed_technical_run_count: int = 0
    canceled_run_count: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float = 0
    avg_seconds_per_word: float = 0
    is_complete: bool = False


class BatchJobIssueOut(BaseModel):
    run_id: str
    entry_id: str
    word: str
    part_of_sentence: str = ""
    category: str = ""
    status: str
    quality_score: float | None = None
    error_detail: str = ""
    reason: str = ""
    updated_at: datetime


class BatchJobReportOut(BaseModel):
    batch_id: str
    status: str
    run_count: int
    completed_run_count: int
    terminal_run_count: int
    passed_run_count: int = 0
    below_threshold_run_count: int = 0
    failed_technical_run_count: int = 0
    canceled_run_count: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float = 0
    avg_seconds_per_word: float = 0
    is_complete: bool = False
    issues: list[BatchJobIssueOut] = Field(default_factory=list)
    reason_counts: dict[str, int] = Field(default_factory=dict)


class RunOut(BaseModel):
    id: str
    entry_id: str
    word: str = ""
    part_of_sentence: str = ""
    category: str = ""
    batch: str = ""
    batch_job: BatchJobSummaryOut | None = None
    status: str
    current_stage: str
    quality_score: float | None
    quality_threshold: int
    optimization_attempt: int
    max_optimization_attempts: int
    technical_retry_count: int
    error_detail: str
    estimated_total_cost_usd: float = 0
    estimated_cost_per_image_usd: float | None = None
    image_count: int = 0
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


class RunEventOut(BaseModel):
    id: str
    stage_name: str
    attempt: int
    event_type: str
    status: str
    message: str
    payload_json: dict[str, Any]
    created_at: datetime


class RunDetailOut(BaseModel):
    run: RunOut
    stages: list[StageResultOut]
    events: list[RunEventOut]
    prompts: list[PromptOut]
    assets: list[AssetOut]
    scores: list[ScoreOut]
    cost_summary: dict[str, Any] = Field(default_factory=dict)
    execution_log: str = ""
    detailed_execution_log: str = ""


class RetryRunResponse(BaseModel):
    run_id: str
    status: str
    retry_from_stage: str


class StopRunResponse(BaseModel):
    run_id: str
    status: str
    current_stage: str
    message: str = ""


class DeleteRunsResponse(BaseModel):
    deleted_run_count: int
    deleted_run_ids: list[str] = Field(default_factory=list)


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
    package_zip_path: str
    manifest_path: str
    csv_download_url: str
    white_bg_zip_download_url: str
    with_bg_zip_download_url: str
    package_zip_download_url: str
    manifest_download_url: str
    error_detail: str
    created_at: datetime
    updated_at: datetime


class CsvJobImportResponse(BaseModel):
    job_id: str
    batch_id: str
    status: str
    imported_count: int
    skipped_count: int
    execution_mode: ExecutionMode
    rows: list[EntryImportRowResult] = Field(default_factory=list)


class CsvJobOut(BaseModel):
    id: str
    batch_id: str
    execution_mode: ExecutionMode
    source_file_name: str = ""
    status: str
    error_detail: str = ""
    total_row_count: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float = 0
    created_at: datetime
    updated_at: datetime


class CsvJobTaskOut(BaseModel):
    id: str
    csv_job_item_id: str
    step_name: str
    task_key: str
    profile_key: str = ""
    source_profile_key: str = ""
    branch_role: str = ""
    status: str
    attempt_count: int
    max_attempts: int
    error_summary: str = ""
    regular_asset_id: str | None = None
    white_bg_asset_id: str | None = None
    dependency_task_ids: list[str] = Field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CsvJobItemProgressOut(BaseModel):
    completed: int = 0
    total: int = 0
    running: int = 0
    waiting: int = 0
    failed: int = 0
    canceled: int = 0


class CsvJobItemOut(BaseModel):
    id: str
    entry_id: str
    row_index: int
    word: str
    part_of_sentence: str
    category: str
    status: str
    error_detail: str = ""
    shadow_run_id: str | None = None
    base_regular_asset_id: str | None = None
    base_white_bg_asset_id: str | None = None
    main_status: str = "pending"
    sub_status: str = ""
    current_step: str = ""
    blocking_reason: str = ""
    waiting_on_steps: list[str] = Field(default_factory=list)
    progress: CsvJobItemProgressOut = Field(default_factory=CsvJobItemProgressOut)
    created_at: datetime
    updated_at: datetime


class CsvJobOverviewOut(BaseModel):
    job: CsvJobOut
    step_counts: dict[str, dict[str, int]] = Field(default_factory=dict)
    issues_by_step: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    items: list[CsvJobItemOut] = Field(default_factory=list)
    tasks: list[CsvJobTaskOut] = Field(default_factory=list)
    word_counts: dict[str, int] = Field(default_factory=dict)
    export_ready: bool = False
    export_id: str | None = None


class CsvJobStartResponse(BaseModel):
    job_id: str
    status: str


class CsvJobRetryResponse(BaseModel):
    job_id: str
    requeued_task_count: int
    status: str


class CsvJobCancelResponse(BaseModel):
    job_id: str
    status: str
    canceled_task_count: int


class CsvJobClearResponse(BaseModel):
    deleted_job_count: int


class CsvJobInventorySyncResponse(BaseModel):
    job_id: str
    synced_row_count: int
    inventory_enabled: bool


class CsvJobExportResponse(BaseModel):
    job_id: str
    batch_id: str
    file_name: str
    zip_path: str
    download_url: str


class RuntimeConfigOut(BaseModel):
    quality_threshold: int
    max_optimization_loops: int
    max_api_retries: int
    stage_retry_limit: int
    worker_poll_seconds: float
    max_parallel_runs: int
    max_variant_workers: int
    flux_imagen_fallback_enabled: bool
    openai_assistant_id: str
    openai_assistant_name: str
    prompt_engineer_mode: Literal["assistant", "responses_api"]
    responses_prompt_engineer_model: PromptEngineerModel
    responses_vector_store_id: str
    visual_style_id: str
    visual_style_name: str
    visual_style_prompt_block: str
    stage1_prompt_template: str
    stage3_prompt_template: str
    openai_model_vision: str
    stage3_critique_model: Literal["gpt-4o-mini", "gpt-5.4", "gemini-3-flash", "gemini-3-pro"]
    stage3_generate_model: Literal["flux-1.1-pro", "imagen-3", "imagen-4", "nano-banana", "nano-banana-2", "nano-banana-pro"]
    quality_gate_model: Literal["gpt-4o-mini", "gemini-3-flash", "gemini-3-pro"]
    image_aspect_ratio: ImageAspectRatio
    image_resolution: ImageResolution
    image_format: ImageFormat
    nano_banana_safety_level: NanoBananaSafetyLevel


class RuntimeConfigUpdate(BaseModel):
    quality_threshold: int | None = Field(default=None, ge=95)
    max_optimization_loops: int | None = None
    max_api_retries: int | None = None
    stage_retry_limit: int | None = None
    worker_poll_seconds: float | None = None
    max_parallel_runs: int | None = Field(default=None, ge=1, le=50)
    max_variant_workers: int | None = Field(default=None, ge=1, le=16)
    flux_imagen_fallback_enabled: bool | None = None
    openai_assistant_id: str | None = None
    openai_assistant_name: str | None = None
    prompt_engineer_mode: Literal["assistant", "responses_api"] | None = None
    responses_prompt_engineer_model: PromptEngineerModel | None = None
    responses_vector_store_id: str | None = None
    visual_style_id: str | None = None
    visual_style_name: str | None = None
    visual_style_prompt_block: str | None = None
    stage1_prompt_template: str | None = None
    stage3_prompt_template: str | None = None
    openai_model_vision: str | None = None
    stage3_critique_model: Literal["gpt-4o-mini", "gpt-5.4", "gemini-3-flash", "gemini-3-pro"] | None = None
    stage3_generate_model: Literal["flux-1.1-pro", "imagen-3", "imagen-4", "nano-banana", "nano-banana-2", "nano-banana-pro"] | None = None
    quality_gate_model: Literal["gpt-4o-mini", "gemini-3-flash", "gemini-3-pro"] | None = None
    image_aspect_ratio: ImageAspectRatio | None = None
    image_resolution: ImageResolution | None = None
    image_format: ImageFormat | None = None
    nano_banana_safety_level: NanoBananaSafetyLevel | None = None
