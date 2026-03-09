from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.models import Base, RuntimeConfig
from app.services.prompt_templates import (
    DEFAULT_STAGE1_PROMPT_TEMPLATE,
    DEFAULT_STAGE3_PROMPT_TEMPLATE,
    DEFAULT_VISUAL_STYLE_ID,
    DEFAULT_VISUAL_STYLE_NAME,
    DEFAULT_VISUAL_STYLE_PROMPT_BLOCK,
)


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
                max_parallel_runs=2,
                flux_imagen_fallback_enabled=True,
                openai_assistant_id="asst_test",
                openai_assistant_name="Prompt generator -JSON output",
                prompt_engineer_mode="responses_api",
                responses_prompt_engineer_model="gpt-5.4",
                responses_vector_store_id="vs_683f3d36223481919f59fc5623286253",
                visual_style_id=DEFAULT_VISUAL_STYLE_ID,
                visual_style_name=DEFAULT_VISUAL_STYLE_NAME,
                visual_style_prompt_block=DEFAULT_VISUAL_STYLE_PROMPT_BLOCK,
                stage1_prompt_template=DEFAULT_STAGE1_PROMPT_TEMPLATE,
                stage3_prompt_template=DEFAULT_STAGE3_PROMPT_TEMPLATE,
                stage3_critique_model="gpt-4o-mini",
                stage3_generate_model="nano-banana-2",
                quality_gate_model="gpt-4o-mini",
                openai_model_vision="gpt-4o-mini",
            )
        )
        session.commit()
        yield session
