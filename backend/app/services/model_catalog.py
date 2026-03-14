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
    "gpt-5.4",
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

SUPPORTED_IMAGE_ASPECT_RATIOS = {
    "1:1",
    "2:3",
    "3:2",
    "3:4",
    "4:3",
    "9:16",
    "16:9",
    "21:9",
}

SUPPORTED_IMAGE_RESOLUTIONS = {
    "1K",
    "2K",
    "4K",
}

SUPPORTED_IMAGE_FORMATS = {
    "image/png",
    "image/jpeg",
    "image/webp",
}

GOOGLE_IMAGE_MODEL_BY_SELECTION = {
    "nano-banana": "gemini-2.5-flash-image",
    "nano-banana-2": "gemini-3.1-flash-image-preview",
    "nano-banana-pro": "gemini-3-pro-image-preview",
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


def is_google_image_generation_model(model: str) -> bool:
    return normalize_stage3_generation_model(model) in GOOGLE_IMAGE_MODEL_BY_SELECTION


def google_image_model_name(model: str) -> str:
    normalized = normalize_stage3_generation_model(model)
    return GOOGLE_IMAGE_MODEL_BY_SELECTION.get(normalized, GOOGLE_IMAGE_MODEL_BY_SELECTION["nano-banana-2"])


def normalize_image_aspect_ratio(value: str) -> str:
    normalized = str(value or "").strip()
    if normalized not in SUPPORTED_IMAGE_ASPECT_RATIOS:
        return "1:1"
    return normalized


def normalize_image_resolution(value: str) -> str:
    normalized = str(value or "").strip().upper()
    if normalized not in SUPPORTED_IMAGE_RESOLUTIONS:
        return "1K"
    return normalized


def normalize_image_format(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in SUPPORTED_IMAGE_FORMATS:
        return "image/png"
    return normalized


def is_gemini_model(model: str) -> bool:
    return normalize_vision_model(model).startswith("gemini-")


def normalize_prompt_engineer_model(model: str) -> str:
    normalized = str(model or "").strip().lower()
    normalized = VISION_MODEL_ALIASES.get(normalized, normalized)
    if normalized not in SUPPORTED_PROMPT_ENGINEER_MODELS:
        return "gpt-5.4"
    return normalized
