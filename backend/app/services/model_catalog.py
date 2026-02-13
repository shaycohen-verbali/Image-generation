from __future__ import annotations

VISION_MODEL_ALIASES = {
    "gpt-40-mini": "gpt-4o-mini",
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
        return "flux-1.1-pro"
    return normalized


def is_gemini_model(model: str) -> bool:
    return normalize_vision_model(model).startswith("gemini-")
