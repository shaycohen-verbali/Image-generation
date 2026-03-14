from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.utcnow()


class Entry(Base):
    __tablename__ = "entries"
    __table_args__ = (
        UniqueConstraint("word", "part_of_sentence", "category", name="uq_entries_word_pos_category"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    word: Mapped[str] = mapped_column(String(256), nullable=False)
    part_of_sentence: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str] = mapped_column(String(256), nullable=False)
    context: Mapped[str] = mapped_column(Text, default="", nullable=False)
    boy_or_girl: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    person_gender_options_json: Mapped[str] = mapped_column(Text, default='["male"]', nullable=False)
    person_age_options_json: Mapped[str] = mapped_column(Text, default='["kid"]', nullable=False)
    person_skin_color_options_json: Mapped[str] = mapped_column(Text, default='["white"]', nullable=False)
    batch: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    source_row_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, nullable=False)

    runs: Mapped[list[Run]] = relationship(back_populates="entry", cascade="all, delete-orphan")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: f"run_{uuid.uuid4().hex[:24]}")
    entry_id: Mapped[str] = mapped_column(ForeignKey("entries.id", ondelete="CASCADE"), nullable=False, index=True)
    execution_mode: Mapped[str] = mapped_column(String(32), default="legacy", nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(64), default="queued", nullable=False, index=True)
    current_stage: Mapped[str] = mapped_column(String(64), default="queued", nullable=False)
    retry_from_stage: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality_threshold: Mapped[int] = mapped_column(Integer, default=95, nullable=False)
    optimization_attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_optimization_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    technical_retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_detail: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, nullable=False)

    entry: Mapped[Entry] = relationship(back_populates="runs")
    stage_results: Mapped[list[StageResult]] = relationship(back_populates="run", cascade="all, delete-orphan")
    events: Mapped[list[RunEvent]] = relationship(back_populates="run", cascade="all, delete-orphan")
    prompts: Mapped[list[Prompt]] = relationship(back_populates="run", cascade="all, delete-orphan")
    assets: Mapped[list[Asset]] = relationship(back_populates="run", cascade="all, delete-orphan")
    scores: Mapped[list[Score]] = relationship(back_populates="run", cascade="all, delete-orphan")


class StageResult(Base):
    __tablename__ = "stage_results"
    __table_args__ = (
        UniqueConstraint("run_id", "stage_name", "attempt", name="uq_stage_results_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: f"stg_{uuid.uuid4().hex[:24]}")
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True)
    stage_name: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    response_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    error_detail: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)

    run: Mapped[Run] = relationship(back_populates="stage_results")


class RunEvent(Base):
    __tablename__ = "run_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: f"evt_{uuid.uuid4().hex[:24]}")
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True)
    stage_name: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)

    run: Mapped[Run] = relationship(back_populates="events")


class Prompt(Base):
    __tablename__ = "prompts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: f"prm_{uuid.uuid4().hex[:24]}")
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True)
    stage_name: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    needs_person: Mapped[str] = mapped_column(String(8), default="", nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="assistant", nullable=False)
    raw_response_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)

    run: Mapped[Run] = relationship(back_populates="prompts")


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: f"ast_{uuid.uuid4().hex[:24]}")
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True)
    stage_name: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    abs_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(64), default="image/jpeg", nullable=False)
    sha256: Mapped[str] = mapped_column(String(128), nullable=False)
    width: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    height: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    origin_url: Mapped[str] = mapped_column(String(2048), default="", nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)

    run: Mapped[Run] = relationship(back_populates="assets")


class Score(Base):
    __tablename__ = "scores"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: f"scr_{uuid.uuid4().hex[:24]}")
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True)
    stage_name: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    score_0_100: Mapped[float] = mapped_column(Float, nullable=False)
    pass_fail: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rubric_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)

    run: Mapped[Run] = relationship(back_populates="scores")


class Export(Base):
    __tablename__ = "exports"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: f"exp_{uuid.uuid4().hex[:24]}")
    filter_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    csv_path: Mapped[str] = mapped_column(String(2048), default="", nullable=False)
    zip_path: Mapped[str] = mapped_column(String(2048), default="", nullable=False)
    manifest_path: Mapped[str] = mapped_column(String(2048), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="pending", nullable=False)
    error_detail: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, nullable=False)


class CsvJob(Base):
    __tablename__ = "csv_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: f"csvjob_{uuid.uuid4().hex[:24]}")
    batch_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    execution_mode: Mapped[str] = mapped_column(String(32), default="csv_dag", nullable=False)
    source_file_name: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    config_snapshot_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="imported", nullable=False, index=True)
    error_detail: Mapped[str] = mapped_column(Text, default="", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, nullable=False)

    items: Mapped[list[CsvJobItem]] = relationship(back_populates="job", cascade="all, delete-orphan")
    tasks: Mapped[list[CsvTaskNode]] = relationship(back_populates="job", cascade="all, delete-orphan")


class CsvJobItem(Base):
    __tablename__ = "csv_job_items"
    __table_args__ = (
        UniqueConstraint("csv_job_id", "row_index", name="uq_csv_job_items_row"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: f"csvitm_{uuid.uuid4().hex[:24]}")
    csv_job_id: Mapped[str] = mapped_column(ForeignKey("csv_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    entry_id: Mapped[str] = mapped_column(ForeignKey("entries.id", ondelete="CASCADE"), nullable=False, index=True)
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    source_row_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    shadow_run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"), nullable=True, index=True)
    base_regular_asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="SET NULL"), nullable=True)
    base_white_bg_asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="pending", nullable=False, index=True)
    error_detail: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, nullable=False)

    job: Mapped[CsvJob] = relationship(back_populates="items")
    entry: Mapped[Entry] = relationship()
    shadow_run: Mapped[Run | None] = relationship()
    base_regular_asset: Mapped[Asset | None] = relationship(foreign_keys=[base_regular_asset_id])
    base_white_bg_asset: Mapped[Asset | None] = relationship(foreign_keys=[base_white_bg_asset_id])
    tasks: Mapped[list[CsvTaskNode]] = relationship(back_populates="job_item", cascade="all, delete-orphan")


class CsvTaskNode(Base):
    __tablename__ = "csv_task_nodes"
    __table_args__ = (
        UniqueConstraint("csv_job_id", "task_key", name="uq_csv_task_nodes_key"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: f"csvtsk_{uuid.uuid4().hex[:24]}")
    csv_job_id: Mapped[str] = mapped_column(ForeignKey("csv_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    csv_job_item_id: Mapped[str] = mapped_column(ForeignKey("csv_job_items.id", ondelete="CASCADE"), nullable=False, index=True)
    step_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    task_key: Mapped[str] = mapped_column(String(256), nullable=False)
    profile_key: Mapped[str] = mapped_column(String(128), default="", nullable=False, index=True)
    source_profile_key: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    branch_role: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    dependency_keys_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    dependency_task_ids_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    source_asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="SET NULL"), nullable=True)
    regular_asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="SET NULL"), nullable=True)
    white_bg_asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="queued", nullable=False, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    error_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, nullable=False)

    job: Mapped[CsvJob] = relationship(back_populates="tasks")
    job_item: Mapped[CsvJobItem] = relationship(back_populates="tasks")
    source_asset: Mapped[Asset | None] = relationship(foreign_keys=[source_asset_id])
    regular_asset: Mapped[Asset | None] = relationship(foreign_keys=[regular_asset_id])
    white_bg_asset: Mapped[Asset | None] = relationship(foreign_keys=[white_bg_asset_id])
    attempts: Mapped[list[CsvTaskAttempt]] = relationship(back_populates="task", cascade="all, delete-orphan")


class CsvTaskAttempt(Base):
    __tablename__ = "csv_task_attempts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: f"csvatt_{uuid.uuid4().hex[:24]}")
    csv_task_node_id: Mapped[str] = mapped_column(ForeignKey("csv_task_nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="running", nullable=False)
    request_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    response_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    error_detail: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)

    task: Mapped[CsvTaskNode] = relationship(back_populates="attempts")


class RuntimeConfig(Base):
    __tablename__ = "runtime_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    quality_threshold: Mapped[int] = mapped_column(Integer, default=95, nullable=False)
    max_optimization_loops: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    max_api_retries: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    stage_retry_limit: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    worker_poll_seconds: Mapped[float] = mapped_column(Float, default=2.0, nullable=False)
    max_parallel_runs: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    max_variant_workers: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    flux_imagen_fallback_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    openai_assistant_id: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    openai_assistant_name: Mapped[str] = mapped_column(String(256), default="Prompt generator -JSON output", nullable=False)
    prompt_engineer_mode: Mapped[str] = mapped_column(String(32), default="responses_api", nullable=False)
    responses_prompt_engineer_model: Mapped[str] = mapped_column(String(128), default="gpt-5.4", nullable=False)
    responses_vector_store_id: Mapped[str] = mapped_column(String(128), default="vs_683f3d36223481919f59fc5623286253", nullable=False)
    visual_style_id: Mapped[str] = mapped_column(String(128), default="warm_watercolor_storybook_kids_v3", nullable=False)
    visual_style_name: Mapped[str] = mapped_column(String(256), default="Warm Watercolor Storybook Kids Style v3", nullable=False)
    visual_style_prompt_block: Mapped[str] = mapped_column(Text, default="", nullable=False)
    stage1_prompt_template: Mapped[str] = mapped_column(Text, default="", nullable=False)
    stage3_prompt_template: Mapped[str] = mapped_column(Text, default="", nullable=False)
    stage3_critique_model: Mapped[str] = mapped_column(String(128), default="gpt-5.4", nullable=False)
    stage3_generate_model: Mapped[str] = mapped_column(String(128), default="nano-banana-2", nullable=False)
    quality_gate_model: Mapped[str] = mapped_column(String(128), default="gpt-4o-mini", nullable=False)
    image_aspect_ratio: Mapped[str] = mapped_column(String(16), default="1:1", nullable=False)
    image_resolution: Mapped[str] = mapped_column(String(8), default="1K", nullable=False)
    image_format: Mapped[str] = mapped_column(String(32), default="image/jpeg", nullable=False)
    nano_banana_safety_level: Mapped[str] = mapped_column(String(32), default="default", nullable=False)
    openai_model_vision: Mapped[str] = mapped_column(String(128), default="gpt-5.4", nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, nullable=False)
