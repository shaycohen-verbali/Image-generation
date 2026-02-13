from sqlalchemy import select, text

from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.models import Base, RuntimeConfig
from app.services.model_catalog import normalize_stage3_generation_model, normalize_vision_model

MIN_QUALITY_THRESHOLD = 95
MIN_PARALLEL_RUNS = 1
DEFAULT_PARALLEL_RUNS = 10


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _ensure_runtime_config_columns()
    settings = get_settings()
    with SessionLocal() as db:
        existing = db.execute(select(RuntimeConfig).where(RuntimeConfig.id == 1)).scalar_one_or_none()
        if existing is None:
            db.add(
                RuntimeConfig(
                    id=1,
                    quality_threshold=max(MIN_QUALITY_THRESHOLD, int(settings.quality_threshold)),
                    max_optimization_loops=settings.max_optimization_loops,
                    max_api_retries=settings.max_api_retries,
                    stage_retry_limit=settings.stage_retry_limit,
                    worker_poll_seconds=settings.worker_poll_seconds,
                    max_parallel_runs=max(MIN_PARALLEL_RUNS, int(settings.max_parallel_runs)),
                    flux_imagen_fallback_enabled=settings.flux_imagen_fallback_enabled,
                    openai_assistant_id=settings.openai_assistant_id,
                    openai_assistant_name=settings.openai_assistant_name,
                    stage3_critique_model=normalize_vision_model(settings.stage3_critique_model or settings.openai_model_vision),
                    stage3_generate_model=normalize_stage3_generation_model(settings.stage3_generate_model),
                    quality_gate_model=normalize_vision_model(settings.quality_gate_model or settings.openai_model_vision),
                    openai_model_vision=normalize_vision_model(settings.openai_model_vision),
                )
            )
            db.commit()
        else:
            if int(existing.quality_threshold) < MIN_QUALITY_THRESHOLD:
                existing.quality_threshold = MIN_QUALITY_THRESHOLD
            if int(existing.max_parallel_runs) < MIN_PARALLEL_RUNS:
                existing.max_parallel_runs = DEFAULT_PARALLEL_RUNS
            existing.stage3_critique_model = normalize_vision_model(existing.stage3_critique_model or existing.openai_model_vision)
            existing.stage3_generate_model = normalize_stage3_generation_model(existing.stage3_generate_model)
            existing.quality_gate_model = normalize_vision_model(existing.quality_gate_model or existing.openai_model_vision)
            existing.openai_model_vision = normalize_vision_model(existing.openai_model_vision)
            db.add(existing)
            db.commit()


def _ensure_runtime_config_columns() -> None:
    if not str(engine.url).startswith("sqlite"):
        return
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(runtime_config)")).fetchall()
        existing = {row[1] for row in rows}
        if "max_parallel_runs" not in existing:
            conn.execute(text("ALTER TABLE runtime_config ADD COLUMN max_parallel_runs INTEGER NOT NULL DEFAULT 10"))
        if "stage3_critique_model" not in existing:
            conn.execute(text("ALTER TABLE runtime_config ADD COLUMN stage3_critique_model TEXT NOT NULL DEFAULT 'gpt-4o-mini'"))
        if "stage3_generate_model" not in existing:
            conn.execute(text("ALTER TABLE runtime_config ADD COLUMN stage3_generate_model TEXT NOT NULL DEFAULT 'flux-1.1-pro'"))
        if "quality_gate_model" not in existing:
            conn.execute(text("ALTER TABLE runtime_config ADD COLUMN quality_gate_model TEXT NOT NULL DEFAULT 'gpt-4o-mini'"))


if __name__ == "__main__":
    init_db()
