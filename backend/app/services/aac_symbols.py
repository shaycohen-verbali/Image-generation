"""Fixed AAC symbol style and the single additional output profile.

``symbol`` is a reserved profile option, not a demographic age. Keeping a
three-part key lets existing task, inventory and export identities stay stable.
"""
import json
from pathlib import Path
from typing import Any

SYMBOL_OPTION = "symbol"
SYMBOL_PROFILE = {"gender": "male", "age": SYMBOL_OPTION, "skin_color": "white"}
SYMBOL_BASE_PROFILE = {"gender": "male", "age": "kid", "skin_color": "white"}
STYLE_VERSION = "soft-signal-v1"
ASSET_DIRECTORY = Path(__file__).resolve().parents[1] / "assets" / "aac_symbols"


def is_symbol_profile(profile: dict[str, str]) -> bool:
    return profile.get("age") == SYMBOL_OPTION


def selected_output_profiles(genders: list[str], ages: list[str], skins: list[str]) -> list[dict[str, str]]:
    profiles = [
        {"gender": gender, "age": age, "skin_color": skin}
        for gender in genders for age in ages if age != SYMBOL_OPTION for skin in skins
    ]
    if SYMBOL_OPTION in ages:
        profiles.append(dict(SYMBOL_PROFILE))
    return profiles


def symbol_meaning(entry: Any) -> str:
    return "; ".join(str(value).strip() for value in (
        entry.word, getattr(entry, "part_of_sentence", ""),
        getattr(entry, "category", ""), getattr(entry, "context", ""),
    ) if value and str(value).strip())


def symbol_reference_path() -> Path:
    path = ASSET_DIRECTORY / "style-reference-v1.png"
    if not path.is_file():
        raise RuntimeError("AAC symbols style reference is missing; restore the bundled v1 reference")
    return path


def symbol_conversion_prompt(meaning: str, *, white_background: bool = False) -> str:
    if white_background:
        return (
            "Keep this exact AAC symbol unchanged, including its subjects, expression, gesture, "
            "object count, rounded shapes, outline weight, flat palette, scale and framing. "
            "Only ensure the background is pure white. Do not introduce a person into an object symbol. "
            "Do not introduce furniture, scenery, text, shadows, extra people or decorative marks. "
            f"Preserve the meaning: {meaning}. Return one symbol on a square white canvas."
        )
    template = (ASSET_DIRECTORY / "conversion-prompt-v1.txt").read_text(encoding="utf-8")
    return template.replace("[REPLACE THIS LINE WITH THE WORD / INTENDED MEANING]", meaning).replace(
        "01-style-reference-v1.png", "style-reference-v1.png"
    )


def check_symbol_meaning(client: Any, source: Path, symbol: Path, *, meaning: str, model: str) -> dict[str, Any]:
    """Check essential meaning without depending on demographic review infrastructure."""
    extracted, extraction_raw = client._vision_json(
        image_path=source, model=model, temperature=0.0,
        prompt=(
            f"Inspect this source AAC image for the intended meaning: {meaning}. "
            "Return JSON with reference_communicates_target (boolean), critical_components "
            "(a nonempty list of objects with unique id and description), person_required_for_target "
            "(boolean: whether a person or body parts are needed to express this target), and summary (string). "
            "List only indispensable subjects, gestures, expressions, identifying features and relationships. "
            "Apply this counterfactual to every proposed component: if it were removed while the other "
            "cues remained, would the exact intended sense still be clear? If yes, exclude it. "
            "Do not require incidental reaching hands, redundant gestures, scenery, furniture, clothing "
            "details or textures. Preserve multiple participants only when the actual relationship requires them."
        ),
    )
    components = extracted.get("critical_components", [])
    valid_components = (
        isinstance(components, list) and bool(components)
        and all(isinstance(c, dict) and c.get("id") and c.get("description") for c in components)
        and len({str(c["id"]) for c in components}) == len(components)
    )
    if (extracted.get("reference_communicates_target") is not True or not valid_components
            or not isinstance(extracted.get("person_required_for_target"), bool)):
        return {"passed": False, "summary": "The source does not establish a clear target meaning",
                "extraction": extracted, "extraction_raw": extraction_raw}
    verified, verification_raw = client._vision_json(
        image_path=symbol, model=model, temperature=0.0,
        prompt=(
            f"Inspect this generated AAC symbol for the intended meaning: {meaning}. "
            f"Essential source components: {json.dumps(components, ensure_ascii=True)}. "
            "Return JSON with symbol_communicates_target (boolean), components (one object for every "
            "source id, with id and status equal to present, missing or uncertain), single_symbol (boolean), "
            "contains_text_or_labels (boolean), contains_unrelated_subjects (boolean), contains_person (boolean), "
            "and summary (string). Accept simplified shapes and changed rendering, but require all "
            "indispensable meaning to remain. There must be exactly ONE symbol composition for the target. "
            "A reference sheet, labeled grid, multiple example icons or neighboring unrelated objects FAIL, "
            "even if the target appears somewhere on that sheet. Do not count required interacting subjects "
            "as separate symbols. Text and copied reference headings or labels always fail."
        ),
    )
    results = verified.get("components", [])
    expected_ids = {str(c["id"]) for c in components}
    valid_results = (
        isinstance(results, list) and len(results) == len(expected_ids)
        and all(isinstance(c, dict) and c.get("status") == "present" for c in results)
        and {str(c.get("id", "")) for c in results} == expected_ids
    )
    format_ok = (
        verified.get("single_symbol") is True
        and verified.get("contains_text_or_labels") is False
        and verified.get("contains_unrelated_subjects") is False
        and isinstance(verified.get("contains_person"), bool)
        and (extracted["person_required_for_target"] or verified["contains_person"] is False)
    )
    passed = verified.get("symbol_communicates_target") is True and valid_results and format_ok
    summary = str(verified.get("summary") or "AAC symbol validation")
    if not format_ok:
        summary = "Expected one unlabeled symbol with no unrelated subjects or unnecessary people. " + summary
    return {"passed": passed, "format_passed": format_ok, "summary": summary,
            "extraction": extracted, "verification": verified,
            "extraction_raw": extraction_raw, "verification_raw": verification_raw}
