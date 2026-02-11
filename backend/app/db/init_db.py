from sqlalchemy import select, text

from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.models import Base, RuntimeConfig


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _ensure_runs_columns()
    settings = get_settings()
    with SessionLocal() as db:
        existing = db.execute(select(RuntimeConfig).where(RuntimeConfig.id == 1)).scalar_one_or_none()
        if existing is None:
            db.add(
                RuntimeConfig(
                    id=1,
                    quality_threshold=settings.quality_threshold,
                    max_optimization_loops=settings.max_optimization_loops,
                    max_api_retries=settings.max_api_retries,
                    stage_retry_limit=settings.stage_retry_limit,
                    worker_poll_seconds=settings.worker_poll_seconds,
                    flux_imagen_fallback_enabled=settings.flux_imagen_fallback_enabled,
                    openai_assistant_id=settings.openai_assistant_id,
                    openai_assistant_name=settings.openai_assistant_name,
                    openai_model_vision=settings.openai_model_vision,
                )
            )
            db.commit()


def _ensure_runs_columns() -> None:
    if not str(engine.url).startswith("sqlite"):
        return

    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(runs)")).fetchall()
        existing = {row[1] for row in rows}
        if "review_warning" not in existing:
            conn.execute(text("ALTER TABLE runs ADD COLUMN review_warning BOOLEAN NOT NULL DEFAULT 0"))
        if "review_warning_reason" not in existing:
            conn.execute(text("ALTER TABLE runs ADD COLUMN review_warning_reason TEXT NOT NULL DEFAULT ''"))


if __name__ == "__main__":
    init_db()
