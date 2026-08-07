from __future__ import annotations

import re
from pathlib import Path

from app.services.utils import sanitize_filename


PATH_FIELD_RE = re.compile(
    r"^(?P<age>[^_]+)_(?P<gender>[^_]+)_(?P<skin>[^_]+)_(?P<background>regular|white_bg)_path$"
)

BACKGROUND_CODES = {
    "regular": "reg",
    "reg": "reg",
    "white_bg": "wbg",
    "white_background": "wbg",
    "wbg": "wbg",
}
GENDER_CODES = {"male": "m", "m": "m", "female": "f", "f": "f"}
AGE_CODES = {
    "toddler": "todd",
    "todd": "todd",
    "kid": "kid",
    "tween": "tween",
    "teenager": "teen",
    "teen": "teen",
}
SKIN_CODES = {
    "white": "w",
    "w": "w",
    "black": "bl",
    "bl": "bl",
    "asian": "a",
    "a": "a",
    "brown": "br",
    "br": "br",
}


def _safe_component(value: object, fallback: str) -> str:
    normalized = str(value or "").strip().lower()
    return sanitize_filename(normalized) if normalized else fallback


def _code(value: object, mapping: dict[str, str], fallback: str) -> str:
    normalized = str(value or "").strip().lower()
    return mapping.get(normalized, _safe_component(normalized, fallback))


def final_image_filename(
    word: object,
    part_of_sentence: object,
    *,
    background: object,
    gender: object,
    age: object,
    skin_color: object,
    sense_id: object,
) -> str:
    """Return the canonical name for a final image or one of its variants."""
    parts = [
        _safe_component(word, "unknown-word"),
        _safe_component(part_of_sentence, "unknown-pos"),
        _code(background, BACKGROUND_CODES, "reg"),
        _code(gender, GENDER_CODES, "m"),
        _code(age, AGE_CODES, "kid"),
        _code(skin_color, SKIN_CODES, "w"),
        _safe_component(sense_id, "no-sense-id"),
    ]
    return "__".join(parts) + ".jpg"


def final_image_filename_for_field(
    word: object,
    part_of_sentence: object,
    field_name: str,
    sense_id: object,
) -> str:
    """Build a canonical final filename from a wide inventory path column."""
    match = PATH_FIELD_RE.match(str(field_name))
    if match is None:
        return ""
    return final_image_filename(
        word,
        part_of_sentence,
        background=match.group("background"),
        gender=match.group("gender"),
        age=match.group("age"),
        skin_color=match.group("skin"),
        sense_id=sense_id,
    )


def inventory_variant(field_name: str) -> str:
    match = PATH_FIELD_RE.match(str(field_name))
    if match is None:
        return str(field_name).removesuffix("_path")
    background = "white_background" if match.group("background") == "white_bg" else "regular"
    return f"{match.group('age')}/{match.group('gender')}/{match.group('skin')}/{background}"


def versioned_upload_filename(path: str, *, canonical_filename: str = "") -> str:
    """Return a safe flat R2 filename with exactly one ``__v1`` suffix."""
    candidate = canonical_filename or Path(str(path or "").replace("\\", "/")).name or "image.jpg"
    candidate = candidate.replace("\\", "_").replace("/", "_")
    stem = re.sub(r"__v\d+$", "", Path(candidate).stem, flags=re.IGNORECASE)
    safe_stem = sanitize_filename(stem) if stem else "image"
    return f"{safe_stem}__v1.jpg"
