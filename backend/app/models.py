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
    batch: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    source_row_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, nullable=False)

    runs: Mapped[list[Run]] = relationship(back_populates="entry", cascade="all, delete-orphan")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: f"run_{uuid.uuid4().hex[:24]}")
    entry_id: Mapped[str] = mapped_column(ForeignKey("entries.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(64), default="queued", nullable=False, index=True)
    current_stage: Mapped[str] = mapped_column(String(64), default="queued", nullable=False)
    retry_from_stage: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality_threshold: Mapped[int] = mapped_column(Integer, default=90, nullable=False)
    optimization_attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_optimization_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    technical_retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    review_warning: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    review_warning_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    error_detail: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, nullable=False)

    entry: Mapped[Entry] = relationship(back_populates="runs")
    stage_results: Mapped[list[StageResult]] = relationship(back_populates="run", cascade="all, delete-orphan")
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


class RuntimeConfig(Base):
    __tablename__ = "runtime_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    quality_threshold: Mapped[int] = mapped_column(Integer, default=90, nullable=False)
    max_optimization_loops: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    max_api_retries: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    stage_retry_limit: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    worker_poll_seconds: Mapped[float] = mapped_column(Float, default=2.0, nullable=False)
    flux_imagen_fallback_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    openai_assistant_id: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    openai_assistant_name: Mapped[str] = mapped_column(String(256), default="Prompt generator -JSON output", nullable=False)
    openai_model_vision: Mapped[str] = mapped_column(String(128), default="gpt-4o-mini", nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, nullable=False)
