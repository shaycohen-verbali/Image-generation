from sqlalchemy import select, text

from app.core.config import get_settings
from app.db.inventory_session import init_inventory_db
from app.db.session import SessionLocal, engine
from app.models import Base, RuntimeConfig
from app.services.model_catalog import (
    normalize_image_aspect_ratio,
    normalize_image_format,
    normalize_image_resolution,
    normalize_nano_banana_safety_level,
    normalize_prompt_engineer_model,
    normalize_stage3_generation_model,
    normalize_vision_model,
)
from app.services.prompt_templates import (
    DEFAULT_STAGE1_PROMPT_TEMPLATE,
    DEFAULT_STAGE3_PROMPT_TEMPLATE,
    DEFAULT_VISUAL_STYLE_ID,
    DEFAULT_VISUAL_STYLE_NAME,
    DEFAULT_VISUAL_STYLE_PROMPT_BLOCK,
)

MIN_QUALITY_THRESHOLD = 95
MIN_PARALLEL_RUNS = 1
DEFAULT_PARALLEL_RUNS = 1
SAFE_PARALLEL_RUNS = 12
MIN_VARIANT_WORKERS = 1
DEFAULT_VARIANT_WORKERS = 2
SAFE_VARIANT_WORKERS = 12


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    init_inventory_db()
    _ensure_entry_columns()
    _ensure_run_columns()
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
                    max_parallel_runs=max(MIN_PARALLEL_RUNS, min(int(settings.max_parallel_runs), SAFE_PARALLEL_RUNS)),
                    max_variant_workers=max(MIN_VARIANT_WORKERS, min(int(settings.max_variant_workers), SAFE_VARIANT_WORKERS)),
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
                    image_aspect_ratio=normalize_image_aspect_ratio(settings.image_aspect_ratio),
                    image_resolution=normalize_image_resolution(settings.image_resolution),
                    image_format=normalize_image_format(settings.image_format),
                    nano_banana_safety_level=normalize_nano_banana_safety_level(settings.nano_banana_safety_level),
                    openai_model_vision=normalize_vision_model(settings.openai_model_vision),
                )
            )
            db.commit()
        else:
            if int(existing.quality_threshold) < MIN_QUALITY_THRESHOLD:
                existing.quality_threshold = MIN_QUALITY_THRESHOLD
            if int(existing.max_parallel_runs) < MIN_PARALLEL_RUNS:
                existing.max_parallel_runs = DEFAULT_PARALLEL_RUNS
            if int(existing.max_parallel_runs) > SAFE_PARALLEL_RUNS:
                existing.max_parallel_runs = SAFE_PARALLEL_RUNS
            if int(getattr(existing, "max_variant_workers", DEFAULT_VARIANT_WORKERS)) < MIN_VARIANT_WORKERS:
                existing.max_variant_workers = DEFAULT_VARIANT_WORKERS
            if int(getattr(existing, "max_variant_workers", DEFAULT_VARIANT_WORKERS)) > SAFE_VARIANT_WORKERS:
                existing.max_variant_workers = SAFE_VARIANT_WORKERS
            existing.stage3_critique_model = normalize_vision_model(existing.stage3_critique_model or existing.openai_model_vision)
            if (
                existing.stage3_critique_model == "gpt-4o-mini"
                and normalize_vision_model(existing.openai_model_vision) == "gpt-4o-mini"
            ):
                existing.stage3_critique_model = "gpt-5.4"
            if not existing.stage3_generate_model or existing.stage3_generate_model == "flux-1.1-pro":
                existing.stage3_generate_model = "nano-banana-2"
            else:
                existing.stage3_generate_model = normalize_stage3_generation_model(existing.stage3_generate_model)
            existing.quality_gate_model = normalize_vision_model(existing.quality_gate_model or existing.openai_model_vision)
            existing.image_aspect_ratio = normalize_image_aspect_ratio(getattr(existing, "image_aspect_ratio", settings.image_aspect_ratio))
            existing.image_resolution = normalize_image_resolution(getattr(existing, "image_resolution", settings.image_resolution))
            existing.image_format = normalize_image_format(getattr(existing, "image_format", settings.image_format))
            existing.nano_banana_safety_level = normalize_nano_banana_safety_level(
                getattr(existing, "nano_banana_safety_level", settings.nano_banana_safety_level)
            )
            if existing.image_format == "image/png":
                existing.image_format = "image/jpeg"
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
            if existing.openai_model_vision == "gpt-4o-mini" and existing.stage3_critique_model == "gpt-5.4":
                existing.openai_model_vision = "gpt-5.4"
            db.add(existing)
            db.commit()


def _ensure_runtime_config_columns() -> None:
    if not str(engine.url).startswith("sqlite"):
        return
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(runtime_config)")).fetchall()
        existing = {row[1] for row in rows}
        if "max_parallel_runs" not in existing:
            conn.execute(text("ALTER TABLE runtime_config ADD COLUMN max_parallel_runs INTEGER NOT NULL DEFAULT 2"))
        if "max_variant_workers" not in existing:
            conn.execute(text("ALTER TABLE runtime_config ADD COLUMN max_variant_workers INTEGER NOT NULL DEFAULT 2"))
        if "stage3_critique_model" not in existing:
            conn.execute(text("ALTER TABLE runtime_config ADD COLUMN stage3_critique_model TEXT NOT NULL DEFAULT 'gpt-5.4'"))
        if "stage3_generate_model" not in existing:
            conn.execute(text("ALTER TABLE runtime_config ADD COLUMN stage3_generate_model TEXT NOT NULL DEFAULT 'nano-banana-2'"))
        if "quality_gate_model" not in existing:
            conn.execute(text("ALTER TABLE runtime_config ADD COLUMN quality_gate_model TEXT NOT NULL DEFAULT 'gpt-4o-mini'"))
        if "image_aspect_ratio" not in existing:
            conn.execute(text("ALTER TABLE runtime_config ADD COLUMN image_aspect_ratio TEXT NOT NULL DEFAULT '1:1'"))
        if "image_resolution" not in existing:
            conn.execute(text("ALTER TABLE runtime_config ADD COLUMN image_resolution TEXT NOT NULL DEFAULT '1K'"))
        if "image_format" not in existing:
            conn.execute(text("ALTER TABLE runtime_config ADD COLUMN image_format TEXT NOT NULL DEFAULT 'image/jpeg'"))
        if "nano_banana_safety_level" not in existing:
            conn.execute(text("ALTER TABLE runtime_config ADD COLUMN nano_banana_safety_level TEXT NOT NULL DEFAULT 'default'"))
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


def _ensure_entry_columns() -> None:
    if not str(engine.url).startswith("sqlite"):
        return
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(entries)")).fetchall()
        existing = {row[1] for row in rows}
        if "person_gender_options_json" not in existing:
            conn.execute(text("ALTER TABLE entries ADD COLUMN person_gender_options_json TEXT NOT NULL DEFAULT '[\"male\"]'"))
        if "person_age_options_json" not in existing:
            conn.execute(text("ALTER TABLE entries ADD COLUMN person_age_options_json TEXT NOT NULL DEFAULT '[\"kid\"]'"))
        if "person_skin_color_options_json" not in existing:
            conn.execute(text("ALTER TABLE entries ADD COLUMN person_skin_color_options_json TEXT NOT NULL DEFAULT '[\"white\"]'"))


def _ensure_run_columns() -> None:
    if not str(engine.url).startswith("sqlite"):
        return
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(runs)")).fetchall()
        existing = {row[1] for row in rows}
        if "execution_mode" not in existing:
            conn.execute(text("ALTER TABLE runs ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'legacy'"))


if __name__ == "__main__":
    init_db()
