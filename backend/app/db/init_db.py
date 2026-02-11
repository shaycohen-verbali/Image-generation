from sqlalchemy import select, text

from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.models import Base, RuntimeConfig

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
                    openai_model_vision=settings.openai_model_vision,
                )
            )
            db.commit()
        else:
            if int(existing.quality_threshold) < MIN_QUALITY_THRESHOLD:
                existing.quality_threshold = MIN_QUALITY_THRESHOLD
            if int(existing.max_parallel_runs) < MIN_PARALLEL_RUNS:
                existing.max_parallel_runs = DEFAULT_PARALLEL_RUNS
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


if __name__ == "__main__":
    init_db()
