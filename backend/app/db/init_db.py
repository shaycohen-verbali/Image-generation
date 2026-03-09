from sqlalchemy import select, text

from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.models import Base, RuntimeConfig
from app.services.model_catalog import normalize_prompt_engineer_model, normalize_stage3_generation_model, normalize_vision_model
from app.services.prompt_templates import (
    DEFAULT_STAGE1_PROMPT_TEMPLATE,
    DEFAULT_STAGE3_PROMPT_TEMPLATE,
    DEFAULT_VISUAL_STYLE_ID,
    DEFAULT_VISUAL_STYLE_NAME,
    DEFAULT_VISUAL_STYLE_PROMPT_BLOCK,
)

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
                    prompt_engineer_mode=settings.prompt_engineer_mode if settings.prompt_engineer_mode in {"assistant", "responses_api"} else "responses_api",
                    responses_prompt_engineer_model=normalize_prompt_engineer_model(settings.responses_prompt_engineer_model),
                    responses_vector_store_id=settings.responses_vector_store_id,
                    visual_style_id=settings.visual_style_id or DEFAULT_VISUAL_STYLE_ID,
                    visual_style_name=settings.visual_style_name or DEFAULT_VISUAL_STYLE_NAME,
                    visual_style_prompt_block=settings.visual_style_prompt_block or DEFAULT_VISUAL_STYLE_PROMPT_BLOCK,
                    stage1_prompt_template=settings.stage1_prompt_template or DEFAULT_STAGE1_PROMPT_TEMPLATE,
                    stage3_prompt_template=settings.stage3_prompt_template or DEFAULT_STAGE3_PROMPT_TEMPLATE,
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
            if not existing.stage3_generate_model or existing.stage3_generate_model == "flux-1.1-pro":
                existing.stage3_generate_model = "nano-banana-2"
            else:
                existing.stage3_generate_model = normalize_stage3_generation_model(existing.stage3_generate_model)
            existing.quality_gate_model = normalize_vision_model(existing.quality_gate_model or existing.openai_model_vision)
            existing.prompt_engineer_mode = existing.prompt_engineer_mode if existing.prompt_engineer_mode in {"assistant", "responses_api"} else "responses_api"
            if not existing.responses_prompt_engineer_model or existing.responses_prompt_engineer_model == "gpt-4.1-mini":
                existing.responses_prompt_engineer_model = "gpt-5.4"
            else:
                existing.responses_prompt_engineer_model = normalize_prompt_engineer_model(existing.responses_prompt_engineer_model or settings.responses_prompt_engineer_model)
            existing.responses_vector_store_id = existing.responses_vector_store_id or settings.responses_vector_store_id
            existing.visual_style_id = existing.visual_style_id or settings.visual_style_id or DEFAULT_VISUAL_STYLE_ID
            existing.visual_style_name = existing.visual_style_name or settings.visual_style_name or DEFAULT_VISUAL_STYLE_NAME
            existing.visual_style_prompt_block = existing.visual_style_prompt_block or settings.visual_style_prompt_block or DEFAULT_VISUAL_STYLE_PROMPT_BLOCK
            existing.stage1_prompt_template = existing.stage1_prompt_template or DEFAULT_STAGE1_PROMPT_TEMPLATE
            existing.stage3_prompt_template = existing.stage3_prompt_template or DEFAULT_STAGE3_PROMPT_TEMPLATE
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
            conn.execute(text("ALTER TABLE runtime_config ADD COLUMN stage3_generate_model TEXT NOT NULL DEFAULT 'nano-banana-2'"))
        if "quality_gate_model" not in existing:
            conn.execute(text("ALTER TABLE runtime_config ADD COLUMN quality_gate_model TEXT NOT NULL DEFAULT 'gpt-4o-mini'"))
        if "prompt_engineer_mode" not in existing:
            conn.execute(text("ALTER TABLE runtime_config ADD COLUMN prompt_engineer_mode TEXT NOT NULL DEFAULT 'responses_api'"))
        if "responses_prompt_engineer_model" not in existing:
            conn.execute(text("ALTER TABLE runtime_config ADD COLUMN responses_prompt_engineer_model TEXT NOT NULL DEFAULT 'gpt-5.4'"))
        if "responses_vector_store_id" not in existing:
            conn.execute(text("ALTER TABLE runtime_config ADD COLUMN responses_vector_store_id TEXT NOT NULL DEFAULT 'vs_683f3d36223481919f59fc5623286253'"))
        if "visual_style_id" not in existing:
            conn.execute(text("ALTER TABLE runtime_config ADD COLUMN visual_style_id TEXT NOT NULL DEFAULT 'warm_watercolor_storybook_kids_v3'"))
        if "visual_style_name" not in existing:
            conn.execute(text("ALTER TABLE runtime_config ADD COLUMN visual_style_name TEXT NOT NULL DEFAULT 'Warm Watercolor Storybook Kids Style v3'"))
        if "visual_style_prompt_block" not in existing:
            conn.execute(text("ALTER TABLE runtime_config ADD COLUMN visual_style_prompt_block TEXT NOT NULL DEFAULT ''"))
        if "stage1_prompt_template" not in existing:
            conn.execute(text("ALTER TABLE runtime_config ADD COLUMN stage1_prompt_template TEXT NOT NULL DEFAULT ''"))
        if "stage3_prompt_template" not in existing:
            conn.execute(text("ALTER TABLE runtime_config ADD COLUMN stage3_prompt_template TEXT NOT NULL DEFAULT ''"))


if __name__ == "__main__":
    init_db()
