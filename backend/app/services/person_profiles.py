from __future__ import annotations

import json
from itertools import product
from typing import Any

DEFAULT_GENDER = "male"
DEFAULT_AGE = "kid"
DEFAULT_SKIN_COLOR = "white"

GENDER_OPTIONS = ("male", "female")
AGE_OPTIONS = ("toddler", "kid", "tween", "teenager")
SKIN_COLOR_OPTIONS = ("white", "black", "asian", "brown")

AGE_LABELS = {
    "toddler": "toddler (2-4)",
    "kid": "kid (5-9)",
    "tween": "tween (10-14)",
    "teenager": "teenager (15-18)",
}

SKIN_COLOR_LABELS = {
    "white": "White",
    "black": "Black",
    "asian": "Asian",
    "brown": "Brown",
}

GENDER_LABELS = {
    "male": "male",
    "female": "female",
}


def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item or "").strip().lower() for item in value if str(item or "").strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return [text.lower()]
        if isinstance(parsed, list):
            return [str(item or "").strip().lower() for item in parsed if str(item or "").strip()]
    return []


def normalize_option_set(values: Any, allowed: tuple[str, ...], default: str) -> list[str]:
    allowed_set = set(allowed)
    unique = []
    seen: set[str] = set()
    for value in _json_list(values):
        if value not in allowed_set or value in seen:
            continue
        unique.append(value)
        seen.add(value)
    if default not in seen:
        unique.insert(0, default)
    else:
        unique = [default] + [value for value in unique if value != default]
    return unique


def entry_gender_options(entry: Any) -> list[str]:
    values = _json_list(getattr(entry, "person_gender_options_json", None))
    if not values:
        legacy = str(getattr(entry, "boy_or_girl", "") or "").strip().lower()
        if legacy in {"girl", "female"}:
            values = ["female"]
        elif legacy in {"boy", "male"}:
            values = ["male"]
    return normalize_option_set(values, GENDER_OPTIONS, DEFAULT_GENDER)


def entry_age_options(entry: Any) -> list[str]:
    return normalize_option_set(
        getattr(entry, "person_age_options_json", None),
        AGE_OPTIONS,
        DEFAULT_AGE,
    )


def entry_skin_color_options(entry: Any) -> list[str]:
    return normalize_option_set(
        getattr(entry, "person_skin_color_options_json", None),
        SKIN_COLOR_OPTIONS,
        DEFAULT_SKIN_COLOR,
    )


def entry_default_profile(entry: Any) -> dict[str, str]:
    genders = entry_gender_options(entry)
    ages = entry_age_options(entry)
    skins = entry_skin_color_options(entry)
    return {
        "gender": genders[0] if genders else DEFAULT_GENDER,
        "age": ages[0] if ages else DEFAULT_AGE,
        "skin_color": skins[0] if skins else DEFAULT_SKIN_COLOR,
    }


def profile_label(profile: dict[str, str]) -> str:
    gender = GENDER_LABELS.get(profile.get("gender", ""), profile.get("gender", "person"))
    age = AGE_LABELS.get(profile.get("age", ""), profile.get("age", "child"))
    skin = SKIN_COLOR_LABELS.get(profile.get("skin_color", ""), profile.get("skin_color", ""))
    return f"{gender}, {age}, {skin} skin"


def profile_key(profile: dict[str, str]) -> str:
    return f"{profile.get('gender', '')}:{profile.get('age', '')}:{profile.get('skin_color', '')}"


def profile_prompt_fragment(profile: dict[str, str]) -> str:
    gender = str(profile.get("gender", "") or "").strip().lower()
    age = str(profile.get("age", "") or "").strip().lower()
    skin = SKIN_COLOR_LABELS.get(profile.get("skin_color", ""), profile.get("skin_color", ""))

    if gender == "female":
        subject = {
            "toddler": "female toddler girl",
            "kid": "school-age girl",
            "tween": "pre-teen girl",
            "teenager": "teenage girl",
        }.get(age, "female person")
    else:
        subject = {
            "toddler": "male toddler boy",
            "kid": "school-age boy",
            "tween": "pre-teen boy",
            "teenager": "teenage boy",
        }.get(age, "male person")

    age_guidance = {
        "toddler": (
            "make the age unmistakably toddler, around 2 to 4 years old, with a very short small body, a noticeably larger head "
            "relative to the body, short toddler limbs, a tiny torso, a low standing height, round baby-faced cheeks, and preschool-child proportions"
        ),
        "kid": (
            "make the age unmistakably a young kid, around 5 to 9 years old, with elementary-school child proportions, a clearly "
            "young face, a child-sized torso, shorter limbs than a tween, and a body that is taller and older than a toddler but still clearly not pre-teen"
        ),
        "tween": (
            "make the age unmistakably a tween, around 10 to 14 years old, with clearly taller pre-teen proportions, longer limbs, "
            "a longer leg-to-torso ratio, a less baby-faced look, and age cues that read older than a kid but younger than a teenager"
        ),
        "teenager": (
            "make the age unmistakably a teenager, around 15 to 18 years old, with clearly older adolescent proportions, a taller "
            "body, longer limbs, a more mature face, near-adolescent body size, visible adolescent height and proportions, and visual age cues that do not read as a child"
        ),
    }.get(age, "make the age visually obvious")

    gender_guidance = {
        "female": (
            "make the person visibly female in a child-friendly, non-stereotyped way, and keep the face, hairstyle, and overall "
            "presentation clearly readable as female"
        ),
        "male": (
            "make the person visibly male in a child-friendly, non-stereotyped way, and keep the face, hairstyle, and overall "
            "presentation clearly readable as male"
        ),
    }.get(gender, "make the gender visually clear")

    skin_guidance = f"use clearly visible {skin} skin tone cues that remain natural and readable"

    return (
        f"{subject} with {skin} skin; {age_guidance}; {gender_guidance}; {skin_guidance}; "
        "preserve the AAC concept and make the age, gender, and skin-color variation obvious at a glance; "
        "make the age difference visible in full-body proportions and height, not only in the face; "
        "the output must read as exactly one person, not multiple people or duplicated figures"
    )


def all_selected_profiles(entry: Any) -> list[dict[str, str]]:
    genders = entry_gender_options(entry)
    ages = entry_age_options(entry)
    skins = entry_skin_color_options(entry)
    profiles = [
        {"gender": gender, "age": age, "skin_color": skin}
        for gender, age, skin in product(genders, ages, skins)
    ]
    default = entry_default_profile(entry)
    ordered = [default]
    ordered.extend(
        profile for profile in profiles
        if profile["gender"] != default["gender"]
        or profile["age"] != default["age"]
        or profile["skin_color"] != default["skin_color"]
    )
    return ordered


def additional_variant_profiles(entry: Any) -> list[dict[str, str]]:
    profiles = all_selected_profiles(entry)
    return profiles[1:] if profiles else []


def variant_branch_plan(entry: Any) -> dict[str, Any]:
    default = entry_default_profile(entry)
    female_anchor = {
        "gender": "female",
        "age": DEFAULT_AGE,
        "skin_color": DEFAULT_SKIN_COLOR,
    }
    plan = {
        "base_profile": default,
        "male_variants": [],
        "female_seed": None,
        "female_variants": [],
    }
    for profile in additional_variant_profiles(entry):
        key = profile_key(profile)
        if profile.get("gender") == "female":
            if key == profile_key(female_anchor):
                plan["female_seed"] = profile
            else:
                plan["female_variants"].append(profile)
            continue
        plan["male_variants"].append(profile)
    return plan


def dump_option_set(values: list[str]) -> str:
    return json.dumps(values, ensure_ascii=True)
