import json

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
DEFAULT_PARALLEL_RUNS = 4
MIN_VARIANT_WORKERS = 1
DEFAULT_VARIANT_WORKERS = 1


def _ensure_word_meaning_prompt_fields(template: str) -> str:
    updated = str(template or "")
    updated = updated.replace("Category: {category}", "Word sense: {word_sense}")
    updated = updated.replace("category: {category}", "Word sense: {word_sense}")
    updated = updated.replace("Word sense: {category}", "Word sense: {word_sense}")
    updated = updated.replace(
        "The word's category can add information in addition to its PoS.",
        "The word sense can add information in addition to its PoS.",
    )

    synonyms_line = "Word synonyms for better meaning: {word_synonyms_for_better_meaning}"
    lines = [line for line in updated.splitlines() if line.strip() != synonyms_line]
    sense_index = next((index for index, line in enumerate(lines) if "{word_sense}" in line), None)
    if sense_index is None:
        lines.append("Word sense: {word_sense}")
        sense_index = len(lines) - 1
    lines.insert(sense_index + 1, synonyms_line)
    return "\n".join(lines).rstrip() + "\n"


def _postgres_existing_columns(conn, table_name: str) -> set[str]:
    rows = conn.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = :table_name
            """
        ),
        {"table_name": table_name},
    ).fetchall()
    return {str(row[0]) for row in rows}


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    init_inventory_db()
    _ensure_hot_indexes()
    _ensure_inventory_columns()
    _ensure_entry_columns()
    _ensure_run_columns()
    _ensure_csv_job_item_columns()
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
                    max_variant_workers=max(MIN_VARIANT_WORKERS, int(settings.max_variant_workers)),
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
                    stage3_anatomy_critique_model=normalize_vision_model(
                        settings.stage3_anatomy_critique_model or settings.stage3_critique_model or settings.openai_model_vision
                    ),
                    stage3_accessibility_critique_model=normalize_vision_model(
                        settings.stage3_accessibility_critique_model
                        or settings.stage3_anatomy_critique_model
                        or settings.stage3_critique_model
                        or settings.openai_model_vision
                    ),
                    stage3_generate_model=normalize_stage3_generation_model(settings.stage3_generate_model),
                    post_quality_accessibility_critique_model=normalize_vision_model(
                        settings.post_quality_accessibility_critique_model
                        or settings.stage3_accessibility_critique_model
                        or settings.stage3_critique_model
                        or settings.openai_model_vision
                    ),
                    post_quality_accessibility_generate_model=normalize_stage3_generation_model(
                        settings.post_quality_accessibility_generate_model or settings.stage3_generate_model
                    ),
                    variant_critique_model=normalize_vision_model(
                        settings.variant_critique_model or settings.stage3_critique_model or settings.openai_model_vision
                    ),
                    variant_correction_model=normalize_stage3_generation_model(
                        settings.variant_correction_model or settings.stage3_generate_model
                    ),
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
            if int(existing.max_parallel_runs) in {1, 2} and int(getattr(existing, "max_variant_workers", 2)) == 2:
                existing.max_parallel_runs = 4
                existing.max_variant_workers = 1
            if int(existing.quality_threshold) < MIN_QUALITY_THRESHOLD:
                existing.quality_threshold = MIN_QUALITY_THRESHOLD
            if int(existing.max_parallel_runs) < MIN_PARALLEL_RUNS:
                existing.max_parallel_runs = DEFAULT_PARALLEL_RUNS
            if int(getattr(existing, "max_variant_workers", DEFAULT_VARIANT_WORKERS)) < MIN_VARIANT_WORKERS:
                existing.max_variant_workers = DEFAULT_VARIANT_WORKERS
            existing.stage3_critique_model = normalize_vision_model(existing.stage3_critique_model or existing.openai_model_vision)
            existing.stage1_prompt_template = _ensure_word_meaning_prompt_fields(
                existing.stage1_prompt_template or DEFAULT_STAGE1_PROMPT_TEMPLATE
            )
            existing.stage3_prompt_template = _ensure_word_meaning_prompt_fields(
                existing.stage3_prompt_template or DEFAULT_STAGE3_PROMPT_TEMPLATE
            )
            if (
                existing.stage3_critique_model == "gpt-4o-mini"
                and normalize_vision_model(existing.openai_model_vision) == "gpt-4o-mini"
            ):
                existing.stage3_critique_model = "gpt-5.4"
            if existing.stage3_critique_model == "gpt-5.4":
                existing.stage3_critique_model = "gemini-3-flash-preview"
            if not existing.stage3_generate_model or existing.stage3_generate_model in {"flux-1.1-pro", "nano-banana-2"}:
                existing.stage3_generate_model = "gemini-3.1-flash-lite-image"
            else:
                existing.stage3_generate_model = normalize_stage3_generation_model(existing.stage3_generate_model)
            existing.quality_gate_model = normalize_vision_model(existing.quality_gate_model or existing.openai_model_vision)
            existing.image_aspect_ratio = normalize_image_aspect_ratio(getattr(existing, "image_aspect_ratio", settings.image_aspect_ratio))
            if existing.image_aspect_ratio == "1:1" and normalize_image_aspect_ratio(settings.image_aspect_ratio) == "4:3":
                existing.image_aspect_ratio = "4:3"
            existing.image_resolution = normalize_image_resolution(getattr(existing, "image_resolution", settings.image_resolution))
            existing.image_format = normalize_image_format(getattr(existing, "image_format", settings.image_format))
            existing.nano_banana_safety_level = normalize_nano_banana_safety_level(
                getattr(existing, "nano_banana_safety_level", settings.nano_banana_safety_level)
            )
            if existing.image_format == "image/png":
                existing.image_format = "image/jpeg"
            existing.prompt_engineer_mode = existing.prompt_engineer_mode if existing.prompt_engineer_mode in {"assistant", "responses_api"} else "responses_api"
            if not existing.responses_prompt_engineer_model or existing.responses_prompt_engineer_model == "gpt-4.1-mini":
                existing.responses_prompt_engineer_model = "gemini-3-flash-preview"
            else:
                existing.responses_prompt_engineer_model = normalize_prompt_engineer_model(existing.responses_prompt_engineer_model or settings.responses_prompt_engineer_model)
                if existing.responses_prompt_engineer_model == "gpt-5.4":
                    existing.responses_prompt_engineer_model = "gemini-3-flash-preview"
            existing.responses_vector_store_id = existing.responses_vector_store_id or settings.responses_vector_store_id
            existing.visual_style_id = existing.visual_style_id or settings.visual_style_id or DEFAULT_VISUAL_STYLE_ID
            existing.visual_style_name = existing.visual_style_name or settings.visual_style_name or DEFAULT_VISUAL_STYLE_NAME
            existing.visual_style_prompt_block = existing.visual_style_prompt_block or settings.visual_style_prompt_block or DEFAULT_VISUAL_STYLE_PROMPT_BLOCK
            existing.stage1_prompt_template = existing.stage1_prompt_template or DEFAULT_STAGE1_PROMPT_TEMPLATE
            existing.stage3_prompt_template = existing.stage3_prompt_template or DEFAULT_STAGE3_PROMPT_TEMPLATE
            existing.stage3_anatomy_critique_model = normalize_vision_model(
                getattr(existing, "stage3_anatomy_critique_model", existing.stage3_critique_model) or existing.stage3_critique_model
            )
            if existing.stage3_anatomy_critique_model == "gpt-5.4":
                existing.stage3_anatomy_critique_model = "gemini-3.1-flash-lite"
            existing.stage3_accessibility_critique_model = normalize_vision_model(
                getattr(existing, "stage3_accessibility_critique_model", existing.stage3_anatomy_critique_model)
                or existing.stage3_anatomy_critique_model
                or existing.stage3_critique_model
            )
            existing.post_quality_accessibility_critique_model = normalize_vision_model(
                getattr(existing, "post_quality_accessibility_critique_model", existing.stage3_accessibility_critique_model)
                or existing.stage3_accessibility_critique_model
                or existing.stage3_critique_model
            )
            if existing.post_quality_accessibility_critique_model == "gpt-5.4":
                existing.post_quality_accessibility_critique_model = "gemini-3.1-flash-lite"
            existing.post_quality_accessibility_generate_model = normalize_stage3_generation_model(
                getattr(existing, "post_quality_accessibility_generate_model", existing.stage3_generate_model)
                or existing.stage3_generate_model
            )
            if existing.post_quality_accessibility_generate_model == "nano-banana-2":
                existing.post_quality_accessibility_generate_model = "gemini-3.1-flash-lite-image"
            existing.variant_critique_model = normalize_vision_model(
                getattr(existing, "variant_critique_model", existing.stage3_critique_model) or existing.stage3_critique_model
            )
            if existing.variant_critique_model == "gpt-5.4":
                existing.variant_critique_model = "gemini-3.1-flash-lite"
            existing.variant_correction_model = normalize_stage3_generation_model(
                getattr(existing, "variant_correction_model", existing.stage3_generate_model) or existing.stage3_generate_model
            )
            if existing.variant_correction_model == "nano-banana-2":
                existing.variant_correction_model = "gemini-3.1-flash-lite-image"
            existing.quality_gate_model = normalize_vision_model(existing.quality_gate_model)
            if existing.quality_gate_model in {"gpt-4o-mini", "gpt-5.4"}:
                existing.quality_gate_model = "gemini-3.1-flash-lite"
            existing.openai_model_vision = normalize_vision_model(existing.openai_model_vision)
            if existing.openai_model_vision in {"gpt-4o-mini", "gpt-5.4"}:
                existing.openai_model_vision = "gemini-3-flash-preview"
            db.add(existing)
            db.commit()


def _ensure_hot_indexes() -> None:
    with engine.begin() as conn:
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_assets_abs_path ON assets (abs_path)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_runs_execution_mode_created_at ON runs (execution_mode, created_at DESC)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_csv_jobs_status_created_at ON csv_jobs (status, created_at DESC)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_csv_task_nodes_job_status_created_at ON csv_task_nodes (csv_job_id, status, created_at ASC)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_csv_task_nodes_item_status ON csv_task_nodes (csv_job_item_id, status)"))


def _ensure_runtime_config_columns() -> None:
    with engine.begin() as conn:
        if str(engine.url).startswith("sqlite"):
            rows = conn.execute(text("PRAGMA table_info(runtime_config)")).fetchall()
            existing = {row[1] for row in rows}
            if "max_parallel_runs" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN max_parallel_runs INTEGER NOT NULL DEFAULT 4"))
            if "max_variant_workers" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN max_variant_workers INTEGER NOT NULL DEFAULT 1"))
            if "stage3_critique_model" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN stage3_critique_model TEXT NOT NULL DEFAULT 'gemini-3-flash-preview'"))
            if "stage3_anatomy_critique_model" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN stage3_anatomy_critique_model TEXT NOT NULL DEFAULT 'gemini-3.1-flash-lite'"))
            if "stage3_accessibility_critique_model" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN stage3_accessibility_critique_model TEXT NOT NULL DEFAULT 'gpt-5.4'"))
            if "stage3_generate_model" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN stage3_generate_model TEXT NOT NULL DEFAULT 'gemini-3.1-flash-lite-image'"))
            if "post_quality_accessibility_critique_model" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN post_quality_accessibility_critique_model TEXT NOT NULL DEFAULT 'gemini-3.1-flash-lite'"))
            if "post_quality_accessibility_generate_model" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN post_quality_accessibility_generate_model TEXT NOT NULL DEFAULT 'gemini-3.1-flash-lite-image'"))
            if "variant_critique_model" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN variant_critique_model TEXT NOT NULL DEFAULT 'gemini-3.1-flash-lite'"))
            if "variant_correction_model" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN variant_correction_model TEXT NOT NULL DEFAULT 'gemini-3.1-flash-lite-image'"))
            if "quality_gate_model" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN quality_gate_model TEXT NOT NULL DEFAULT 'gemini-3.1-flash-lite'"))
            if "image_aspect_ratio" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN image_aspect_ratio TEXT NOT NULL DEFAULT '4:3'"))
            if "image_resolution" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN image_resolution TEXT NOT NULL DEFAULT '1K'"))
            if "image_format" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN image_format TEXT NOT NULL DEFAULT 'image/jpeg'"))
            if "nano_banana_safety_level" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN nano_banana_safety_level TEXT NOT NULL DEFAULT 'default'"))
            if "prompt_engineer_mode" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN prompt_engineer_mode TEXT NOT NULL DEFAULT 'responses_api'"))
            if "responses_prompt_engineer_model" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN responses_prompt_engineer_model TEXT NOT NULL DEFAULT 'gemini-3-flash-preview'"))
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
        else:
            existing = _postgres_existing_columns(conn, "runtime_config")
            if "max_parallel_runs" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN max_parallel_runs INTEGER NOT NULL DEFAULT 4"))
            if "max_variant_workers" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN max_variant_workers INTEGER NOT NULL DEFAULT 1"))
            if "stage3_critique_model" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN stage3_critique_model TEXT NOT NULL DEFAULT 'gemini-3-flash-preview'"))
            if "stage3_anatomy_critique_model" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN stage3_anatomy_critique_model TEXT NOT NULL DEFAULT 'gemini-3.1-flash-lite'"))
            if "stage3_accessibility_critique_model" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN stage3_accessibility_critique_model TEXT NOT NULL DEFAULT 'gpt-5.4'"))
            if "stage3_generate_model" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN stage3_generate_model TEXT NOT NULL DEFAULT 'gemini-3.1-flash-lite-image'"))
            if "post_quality_accessibility_critique_model" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN post_quality_accessibility_critique_model TEXT NOT NULL DEFAULT 'gemini-3.1-flash-lite'"))
            if "post_quality_accessibility_generate_model" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN post_quality_accessibility_generate_model TEXT NOT NULL DEFAULT 'gemini-3.1-flash-lite-image'"))
            if "variant_critique_model" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN variant_critique_model TEXT NOT NULL DEFAULT 'gemini-3.1-flash-lite'"))
            if "variant_correction_model" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN variant_correction_model TEXT NOT NULL DEFAULT 'gemini-3.1-flash-lite-image'"))
            if "quality_gate_model" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN quality_gate_model TEXT NOT NULL DEFAULT 'gemini-3.1-flash-lite'"))
            if "image_aspect_ratio" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN image_aspect_ratio TEXT NOT NULL DEFAULT '4:3'"))
            if "image_resolution" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN image_resolution TEXT NOT NULL DEFAULT '1K'"))
            if "image_format" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN image_format TEXT NOT NULL DEFAULT 'image/jpeg'"))
            if "nano_banana_safety_level" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN nano_banana_safety_level TEXT NOT NULL DEFAULT 'default'"))
            if "prompt_engineer_mode" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN prompt_engineer_mode TEXT NOT NULL DEFAULT 'responses_api'"))
            if "responses_prompt_engineer_model" not in existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN responses_prompt_engineer_model TEXT NOT NULL DEFAULT 'gemini-3-flash-preview'"))
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


def _ensure_inventory_columns() -> None:
    from app.db.inventory_session import inventory_engine as inv_engine  # avoid circular at module level
    from app.inventory_models import BACKGROUND_VALUES, GENDER_VALUES, SKIN_VALUES, AGE_VALUES, inventory_prompt_column_name
    if inv_engine is None:
        return
    with inv_engine.begin() as conn:
        if str(inv_engine.url).startswith("sqlite"):
            rows = conn.execute(text("PRAGMA table_info(word_inventory)")).fetchall()
            existing = {row[1] for row in rows}
            if "has_person" not in existing:
                conn.execute(text("ALTER TABLE word_inventory ADD COLUMN has_person TEXT NOT NULL DEFAULT ''"))
            if "image_score" not in existing:
                conn.execute(text("ALTER TABLE word_inventory ADD COLUMN image_score REAL"))
            if "needs_person_attention" not in existing:
                conn.execute(text("ALTER TABLE word_inventory ADD COLUMN needs_person_attention BOOLEAN NOT NULL DEFAULT 0"))
            for age in AGE_VALUES:
                for gender in GENDER_VALUES:
                    for skin_color in SKIN_VALUES:
                        for background in BACKGROUND_VALUES:
                            column_name = inventory_prompt_column_name(age, gender, skin_color, background)
                            if column_name not in existing:
                                conn.execute(text(f"ALTER TABLE word_inventory ADD COLUMN {column_name} TEXT NOT NULL DEFAULT ''"))
        else:
            existing = _postgres_existing_columns(conn, "word_inventory")
            if "has_person" not in existing:
                conn.execute(text("ALTER TABLE word_inventory ADD COLUMN has_person TEXT NOT NULL DEFAULT ''"))
            if "image_score" not in existing:
                conn.execute(text("ALTER TABLE word_inventory ADD COLUMN image_score DOUBLE PRECISION"))
            if "needs_person_attention" not in existing:
                conn.execute(text("ALTER TABLE word_inventory ADD COLUMN needs_person_attention BOOLEAN NOT NULL DEFAULT FALSE"))
            for age in AGE_VALUES:
                for gender in GENDER_VALUES:
                    for skin_color in SKIN_VALUES:
                        for background in BACKGROUND_VALUES:
                            column_name = inventory_prompt_column_name(age, gender, skin_color, background)
                            if column_name not in existing:
                                conn.execute(text(f"ALTER TABLE word_inventory ADD COLUMN {column_name} TEXT NOT NULL DEFAULT ''"))


def _ensure_entry_columns() -> None:
    with engine.begin() as conn:
        if str(engine.url).startswith("sqlite"):
            rows = conn.execute(text("PRAGMA table_info(entries)")).fetchall()
            existing = {row[1] for row in rows}
            if "person_gender_options_json" not in existing:
                conn.execute(text("ALTER TABLE entries ADD COLUMN person_gender_options_json TEXT NOT NULL DEFAULT '[\"male\"]'"))
            if "person_age_options_json" not in existing:
                conn.execute(text("ALTER TABLE entries ADD COLUMN person_age_options_json TEXT NOT NULL DEFAULT '[\"kid\"]'"))
            if "person_skin_color_options_json" not in existing:
                conn.execute(text("ALTER TABLE entries ADD COLUMN person_skin_color_options_json TEXT NOT NULL DEFAULT '[\"white\"]'"))
            if "has_person" not in existing:
                conn.execute(text("ALTER TABLE entries ADD COLUMN has_person TEXT NOT NULL DEFAULT ''"))
            if "word_synonyms_for_better_meaning" not in existing:
                conn.execute(text("ALTER TABLE entries ADD COLUMN word_synonyms_for_better_meaning TEXT NOT NULL DEFAULT ''"))
            if "sense_id" not in existing:
                conn.execute(text("ALTER TABLE entries ADD COLUMN sense_id TEXT NOT NULL DEFAULT ''"))
        else:
            existing = _postgres_existing_columns(conn, "entries")
            if "has_person" not in existing:
                conn.execute(text("ALTER TABLE entries ADD COLUMN has_person TEXT NOT NULL DEFAULT ''"))
            if "word_synonyms_for_better_meaning" not in existing:
                conn.execute(text("ALTER TABLE entries ADD COLUMN word_synonyms_for_better_meaning TEXT NOT NULL DEFAULT ''"))
            if "sense_id" not in existing:
                conn.execute(text("ALTER TABLE entries ADD COLUMN sense_id TEXT NOT NULL DEFAULT ''"))

        rows = conn.execute(
            text(
                "SELECT i.entry_id, i.source_row_json "
                "FROM csv_job_items i JOIN entries e ON e.id = i.entry_id "
                "WHERE e.sense_id = ''"
            )
        ).fetchall()
        sense_ids_by_entry: dict[str, str] = {}
        for entry_id, source_row_json in rows:
            try:
                source_row = json.loads(str(source_row_json or "{}"))
            except (TypeError, ValueError):
                continue
            sense_id = str(source_row.get("sense_id") or source_row.get("_word_source_sense_id") or "").strip()
            if sense_id:
                sense_ids_by_entry.setdefault(str(entry_id), sense_id)
        for entry_id, sense_id in sense_ids_by_entry.items():
            conn.execute(
                text("UPDATE entries SET sense_id = :sense_id WHERE id = :entry_id AND sense_id = ''"),
                {"sense_id": sense_id, "entry_id": entry_id},
            )


def _ensure_run_columns() -> None:
    with engine.begin() as conn:
        if str(engine.url).startswith("sqlite"):
            rows = conn.execute(text("PRAGMA table_info(runs)")).fetchall()
            existing = {row[1] for row in rows}
            if "execution_mode" not in existing:
                conn.execute(text("ALTER TABLE runs ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'legacy'"))
        else:
            existing = _postgres_existing_columns(conn, "runs")
            if "execution_mode" not in existing:
                conn.execute(text("ALTER TABLE runs ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'legacy'"))


def _ensure_csv_job_item_columns() -> None:
    with engine.begin() as conn:
        if str(engine.url).startswith("sqlite"):
            rows = conn.execute(text("PRAGMA table_info(csv_job_items)")).fetchall()
            existing = {row[1] for row in rows}
            if "base_soften_asset_id" not in existing:
                conn.execute(text("ALTER TABLE csv_job_items ADD COLUMN base_soften_asset_id TEXT"))
        else:
            existing = _postgres_existing_columns(conn, "csv_job_items")
            if "base_soften_asset_id" not in existing:
                conn.execute(text("ALTER TABLE csv_job_items ADD COLUMN base_soften_asset_id TEXT"))


if __name__ == "__main__":
    init_db()
