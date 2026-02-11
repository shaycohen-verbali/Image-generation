from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.models import Base, RuntimeConfig

MIN_QUALITY_THRESHOLD = 95


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
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
                    flux_imagen_fallback_enabled=settings.flux_imagen_fallback_enabled,
                    openai_assistant_id=settings.openai_assistant_id,
                    openai_assistant_name=settings.openai_assistant_name,
                    openai_model_vision=settings.openai_model_vision,
                )
            )
            db.commit()
        elif int(existing.quality_threshold) < MIN_QUALITY_THRESHOLD:
            existing.quality_threshold = MIN_QUALITY_THRESHOLD
            db.add(existing)
            db.commit()


if __name__ == "__main__":
    init_db()
