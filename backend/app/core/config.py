from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="dev", alias="APP_ENV")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    app_log_level: str = Field(default="INFO", alias="APP_LOG_LEVEL")

    database_url: str = Field(
        default="sqlite:///./runtime_data/aac_image_generator.db",
        alias="SUPABASE_DATABASE_URL",
    )
    inventory_database_url: str = Field(default="", alias="INVENTORY_DATABASE_URL")
    runtime_data_root: Path = Field(
        default=Path("/Users/anna.cohen/Documents/Image generation/runtime_data"),
        alias="RUNTIME_DATA_ROOT",
    )
    storage_backend: str = Field(default="local", alias="STORAGE_BACKEND")
    supabase_url: str = Field(default="", alias="SUPABASE_URL")
    supabase_service_role_key: str = Field(default="", alias="SUPABASE_SERVICE_ROLE_KEY")
    supabase_image_bucket: str = Field(default="generated-images", alias="SUPABASE_IMAGE_BUCKET")
    supabase_sense_images_sync_rpc_url: str = Field(default="", alias="SUPABASE_SENSE_IMAGES_SYNC_RPC_URL")
    supabase_sense_images_sync_function: str = Field(
        default="aac_sync_sense_images_from_inventory",
        alias="SUPABASE_SENSE_IMAGES_SYNC_FUNCTION",
    )
    supabase_sense_images_style: str = Field(default="aac_current", alias="SUPABASE_SENSE_IMAGES_STYLE")
    supabase_sense_images_style_version: str = Field(default="1", alias="SUPABASE_SENSE_IMAGES_STYLE_VERSION")
    supabase_sense_images_bucket: str = Field(default="aac-images-v1", alias="SUPABASE_SENSE_IMAGES_BUCKET")
    supabase_sense_images_sync_batch_size: int = Field(default=100, alias="SUPABASE_SENSE_IMAGES_SYNC_BATCH_SIZE")
    supabase_export_bucket: str = Field(default="exports", alias="SUPABASE_EXPORT_BUCKET")
    supabase_csv_bucket: str = Field(default="csv-imports", alias="SUPABASE_CSV_BUCKET")
    cloudflare_r2_endpoint: str = Field(default="", alias="CLOUDFLARE_R2_ENDPOINT")
    cloudflare_r2_access_key_id: str = Field(default="", alias="CLOUDFLARE_R2_ACCESS_KEY_ID")
    cloudflare_r2_secret_access_key: str = Field(default="", alias="CLOUDFLARE_R2_SECRET_ACCESS_KEY")
    cloudflare_r2_buckets: str = Field(default="matalkimages", alias="CLOUDFLARE_R2_BUCKETS")
    cloudflare_r2_default_bucket: str = Field(default="matalkimages", alias="CLOUDFLARE_R2_DEFAULT_BUCKET")
    cloudflare_r2_key_prefix: str = Field(default="word_inventory", alias="CLOUDFLARE_R2_KEY_PREFIX")
    cloudflare_r2_compression_quality: int = Field(default=79, alias="CLOUDFLARE_R2_COMPRESSION_QUALITY")
    cloudflare_r2_upload_workers: int = Field(default=8, alias="CLOUDFLARE_R2_UPLOAD_WORKERS")
    cloudflare_r2_public_base_url: str = Field(default="", alias="CLOUDFLARE_R2_PUBLIC_BASE_URL")
    slack_bot_token: str = Field(default="", alias="SLACK_BOT_TOKEN")
    slack_signing_secret: str = Field(default="", alias="SLACK_SIGNING_SECRET")
    slack_allowed_user_ids: str = Field(default="", alias="SLACK_ALLOWED_USER_IDS")
    slack_alert_user_id: str = Field(default="", alias="SLACK_ALERT_USER_ID")

    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_assistant_id: str = Field(default="", alias="OPENAI_ASSISTANT_ID")
    openai_assistant_name: str = Field(default="Prompt generator -JSON output", alias="OPENAI_ASSISTANT_NAME")
    prompt_engineer_mode: str = Field(default="responses_api", alias="PROMPT_ENGINEER_MODE")
    responses_prompt_engineer_model: str = Field(default="gemini-3-flash-preview", alias="RESPONSES_PROMPT_ENGINEER_MODEL")
    responses_vector_store_id: str = Field(default="vs_683f3d36223481919f59fc5623286253", alias="RESPONSES_VECTOR_STORE_ID")
    visual_style_id: str = Field(default="warm_watercolor_storybook_kids_v3", alias="VISUAL_STYLE_ID")
    visual_style_name: str = Field(default="Warm Watercolor Storybook Kids Style v3", alias="VISUAL_STYLE_NAME")
    visual_style_prompt_block: str = Field(default="", alias="VISUAL_STYLE_PROMPT_BLOCK")
    stage1_prompt_template: str = Field(default="", alias="STAGE1_PROMPT_TEMPLATE")
    stage3_prompt_template: str = Field(default="", alias="STAGE3_PROMPT_TEMPLATE")
    openai_model_vision: str = Field(default="gemini-3-flash-preview", alias="OPENAI_MODEL_VISION")
    stage3_critique_model: str = Field(default="gemini-3-flash-preview", alias="STAGE3_CRITIQUE_MODEL")
    stage3_anatomy_critique_model: str = Field(default="gemini-3.1-flash-lite", alias="STAGE3_ANATOMY_CRITIQUE_MODEL")
    stage3_accessibility_critique_model: str = Field(default="gpt-5.4", alias="STAGE3_ACCESSIBILITY_CRITIQUE_MODEL")
    stage3_generate_model: str = Field(default="gemini-3.1-flash-lite-image", alias="STAGE3_GENERATE_MODEL")
    post_quality_accessibility_critique_model: str = Field(default="gemini-3.1-flash-lite", alias="POST_QUALITY_ACCESSIBILITY_CRITIQUE_MODEL")
    post_quality_accessibility_generate_model: str = Field(default="gemini-3.1-flash-lite-image", alias="POST_QUALITY_ACCESSIBILITY_GENERATE_MODEL")
    variant_critique_model: str = Field(default="gemini-3.1-flash-lite", alias="VARIANT_CRITIQUE_MODEL")
    variant_correction_model: str = Field(default="gemini-3.1-flash-lite-image", alias="VARIANT_CORRECTION_MODEL")
    quality_gate_model: str = Field(default="gemini-3.1-flash-lite", alias="QUALITY_GATE_MODEL")
    image_aspect_ratio: str = Field(default="4:3", alias="IMAGE_ASPECT_RATIO")
    image_resolution: str = Field(default="1K", alias="IMAGE_RESOLUTION")
    image_format: str = Field(default="image/jpeg", alias="IMAGE_FORMAT")
    nano_banana_safety_level: str = Field(default="default", alias="NANO_BANANA_SAFETY_LEVEL")
    google_api_key: str = Field(default="", alias="GOOGLE_API_KEY")

    replicate_api_token: str = Field(default="", alias="REPLICATE_API_TOKEN")
    replicate_base_url: str = Field(default="https://api.replicate.com", alias="REPLICATE_BASE_URL")

    quality_threshold: int = Field(default=95, alias="QUALITY_THRESHOLD")
    max_optimization_loops: int = Field(default=3, alias="MAX_OPTIMIZATION_LOOPS")
    max_api_retries: int = Field(default=3, alias="MAX_API_RETRIES")
    stage_retry_limit: int = Field(default=3, alias="STAGE_RETRY_LIMIT")
    worker_poll_seconds: float = Field(default=2.0, alias="WORKER_POLL_SECONDS")
    max_parallel_runs: int = Field(default=4, alias="MAX_PARALLEL_RUNS")
    max_variant_workers: int = Field(default=1, alias="MAX_VARIANT_WORKERS")
    flux_imagen_fallback_enabled: bool = Field(default=True, alias="FLUX_IMAGEN_FALLBACK_ENABLED")
    phase7_monitoring_enabled: bool = Field(default=True, alias="PHASE7_MONITORING_ENABLED")
    phase7_monitoring_interval_seconds: int = Field(default=300, alias="PHASE7_MONITORING_INTERVAL_SECONDS")
    phase7_monitoring_query_timeout_ms: int = Field(default=1000, alias="PHASE7_MONITORING_QUERY_TIMEOUT_MS")
    phase7_job_summary_enabled: bool = Field(default=True, alias="PHASE7_JOB_SUMMARY_ENABLED")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.runtime_data_root.mkdir(parents=True, exist_ok=True)
    return settings
