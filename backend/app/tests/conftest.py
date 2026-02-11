from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.models import Base, RuntimeConfig


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    settings = get_settings()
    settings.runtime_data_root = tmp_path / "runtime_data"
    settings.runtime_data_root.mkdir(parents=True, exist_ok=True)

    # Keep storage service in sync with test runtime path.
    import app.services.storage as storage

    storage.settings.runtime_data_root = settings.runtime_data_root

    with SessionLocal() as session:
        session.add(
                RuntimeConfig(
                    id=1,
                    quality_threshold=95,
                    max_optimization_loops=3,
                max_api_retries=3,
                stage_retry_limit=3,
                worker_poll_seconds=0.1,
                flux_imagen_fallback_enabled=True,
                openai_assistant_id="asst_test",
                openai_assistant_name="Prompt generator -JSON output",
                openai_model_vision="gpt-4o-mini",
            )
        )
        session.commit()
        yield session
