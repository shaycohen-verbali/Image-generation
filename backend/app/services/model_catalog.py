from __future__ import annotations

VISION_MODEL_ALIASES = {
    "gpt-40-mini": "gpt-4o-mini",
    "gpt 5.4": "gpt-5.4",
}

SUPPORTED_PROMPT_ENGINEER_MODELS = {
    "gpt-4o-mini",
    "gpt-4.1-mini",
    "gpt-5.4",
    "gemini-3-flash",
    "gemini-3-pro",
}

SUPPORTED_VISION_MODELS = {
    "gpt-4o-mini",
    "gemini-3-flash",
    "gemini-3-pro",
}

SUPPORTED_STAGE3_GENERATION_MODELS = {
    "flux-1.1-pro",
    "imagen-3",
    "imagen-4",
    "nano-banana",
    "nano-banana-2",
    "nano-banana-pro",
}


def normalize_vision_model(model: str) -> str:
    normalized = str(model or "").strip().lower()
    normalized = VISION_MODEL_ALIASES.get(normalized, normalized)
    if normalized not in SUPPORTED_VISION_MODELS:
        return "gpt-4o-mini"
    return normalized


def normalize_stage3_generation_model(model: str) -> str:
    normalized = str(model or "").strip().lower()
    if normalized not in SUPPORTED_STAGE3_GENERATION_MODELS:
        return "nano-banana-2"
    return normalized


def is_gemini_model(model: str) -> bool:
    return normalize_vision_model(model).startswith("gemini-")


def normalize_prompt_engineer_model(model: str) -> str:
    normalized = str(model or "").strip().lower()
    normalized = VISION_MODEL_ALIASES.get(normalized, normalized)
    if normalized not in SUPPORTED_PROMPT_ENGINEER_MODELS:
        return "gpt-5.4"
    return normalized
