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

    database_url: str = Field(default="sqlite:///./runtime_data/aac_image_generator.db", alias="DATABASE_URL")
    runtime_data_root: Path = Field(
        default=Path("/Users/anna.cohen/Documents/Image generation/runtime_data"),
        alias="RUNTIME_DATA_ROOT",
    )

    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_assistant_id: str = Field(default="", alias="OPENAI_ASSISTANT_ID")
    openai_assistant_name: str = Field(default="Prompt generator -JSON output", alias="OPENAI_ASSISTANT_NAME")
    prompt_engineer_mode: str = Field(default="responses_api", alias="PROMPT_ENGINEER_MODE")
    responses_prompt_engineer_model: str = Field(default="gpt-5.4", alias="RESPONSES_PROMPT_ENGINEER_MODEL")
    responses_vector_store_id: str = Field(default="vs_683f3d36223481919f59fc5623286253", alias="RESPONSES_VECTOR_STORE_ID")
    visual_style_id: str = Field(default="warm_watercolor_storybook_kids_v3", alias="VISUAL_STYLE_ID")
    visual_style_name: str = Field(default="Warm Watercolor Storybook Kids Style v3", alias="VISUAL_STYLE_NAME")
    visual_style_prompt_block: str = Field(default="", alias="VISUAL_STYLE_PROMPT_BLOCK")
    stage1_prompt_template: str = Field(default="", alias="STAGE1_PROMPT_TEMPLATE")
    stage3_prompt_template: str = Field(default="", alias="STAGE3_PROMPT_TEMPLATE")
    openai_model_vision: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL_VISION")
    stage3_critique_model: str = Field(default="gpt-4o-mini", alias="STAGE3_CRITIQUE_MODEL")
    stage3_generate_model: str = Field(default="nano-banana-2", alias="STAGE3_GENERATE_MODEL")
    quality_gate_model: str = Field(default="gpt-4o-mini", alias="QUALITY_GATE_MODEL")
    image_aspect_ratio: str = Field(default="1:1", alias="IMAGE_ASPECT_RATIO")
    image_resolution: str = Field(default="1K", alias="IMAGE_RESOLUTION")
    google_api_key: str = Field(default="", alias="GOOGLE_API_KEY")

    replicate_api_token: str = Field(default="", alias="REPLICATE_API_TOKEN")
    replicate_cf_base_url: str = Field(default="", alias="REPLICATE_CF_BASE_URL")

    quality_threshold: int = Field(default=95, alias="QUALITY_THRESHOLD")
    max_optimization_loops: int = Field(default=3, alias="MAX_OPTIMIZATION_LOOPS")
    max_api_retries: int = Field(default=3, alias="MAX_API_RETRIES")
    stage_retry_limit: int = Field(default=3, alias="STAGE_RETRY_LIMIT")
    worker_poll_seconds: float = Field(default=2.0, alias="WORKER_POLL_SECONDS")
    max_parallel_runs: int = Field(default=2, alias="MAX_PARALLEL_RUNS")
    flux_imagen_fallback_enabled: bool = Field(default=True, alias="FLUX_IMAGEN_FALLBACK_ENABLED")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.runtime_data_root.mkdir(parents=True, exist_ok=True)
    return settings
