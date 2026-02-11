from __future__ import annotations

from app.models import Entry

PHOTO_REALISTIC_CATEGORIES = {
    "drinks",
    "animals",
    "food",
    "food: fruits",
    "food: vegetables",
    "food: sweets & desserts",
    "shapes",
    "school supplies",
    "transportation",
}


def _photorealistic_hint() -> str:
    return (
        "If category is one of: Drinks, animals, food, food: fruits, food: vegetables, "
        "food: Sweets & desserts, shapes, school supplies, transportation - use a photorealistic style."
    )


def _abstract_instructions(contrast_subject: str) -> str:
    return (
        "The concept is abstract/ambiguous. Use a single-frame contrast composition. "
        "Show an expected state and an actual state in the same image with very clear visual separation. "
        f"Focus the contrast on this subject: {contrast_subject}. "
        "Make absence/negation obvious with object count and salience cues, not text. "
        "Keep clutter minimal and child-friendly for AAC interpretation."
    )


def build_stage1_prompt(entry: Entry, abstract_intent: dict | None = None) -> str:
    extra_instruction = ""
    if abstract_intent and bool(abstract_intent.get("is_abstract")):
        extra_instruction = f"{_abstract_instructions(str(abstract_intent.get('contrast_subject', 'target object')))}\n"

    return (
        "Task: Create the first image prompt for the given word and decide if the prompt needs a person.\n"
        "Return STRICT JSON with keys exactly:\n"
        '{ "first prompt": "<string>", "need a person": "yes" | "no" }\n\n'
        f"Context: {entry.context}\n"
        f"Word: {entry.word}\n"
        f"Part of speech: {entry.part_of_sentence}\n"
        f"Category: {entry.category}\n"
        f"If a person is present, use a: {entry.boy_or_girl}\n\n"
        f"{extra_instruction}"
        f"{_photorealistic_hint()}\n"
    )


def build_stage3_prompt(
    entry: Entry,
    old_prompt: str,
    challenges: str,
    recommendations: str,
    abstract_intent: dict | None = None,
    reinforce_contrast: bool = False,
) -> str:
    extra_instruction = ""
    if abstract_intent and bool(abstract_intent.get("is_abstract")):
        extra_instruction = _abstract_instructions(str(abstract_intent.get("contrast_subject", "target object")))
        if reinforce_contrast:
            extra_instruction = (
                f"{extra_instruction} "
                "Previous output was ambiguous. Increase expected-vs-actual contrast and simplify irrelevant details."
            )

    return (
        "Create an upgraded image prompt for the given word. Return STRICT JSON:\n"
        '{ "upgraded prompt": "<string>" }\n\n'
        f"context for the image: {entry.context}\n"
        f"Old prompt: {old_prompt}\n"
        f"challenges and improvements with the old image: challenges={challenges}; recommendations={recommendations}\n"
        f"word: {entry.word}\n"
        f"part of sentence: {entry.part_of_sentence}\n"
        f"Category: {entry.category}\n"
        f"If a person is present, use a {entry.boy_or_girl} as the person.\n\n"
        "Do not use text in the image.\n"
        "The word's category can add information in addition to its PoS.\n"
        f"{extra_instruction}\n"
        f"{_photorealistic_hint()}\n"
    )
