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


def build_stage1_prompt(entry: Entry) -> str:
    return (
        "Task: Create the first image prompt for the given word and decide if the prompt needs a person.\n"
        "Return STRICT JSON with keys exactly:\n"
        '{ "first prompt": "<string>", "need a person": "yes" | "no" }\n\n'
        f"Context: {entry.context}\n"
        f"Word: {entry.word}\n"
        f"Part of speech: {entry.part_of_sentence}\n"
        f"Category: {entry.category}\n"
        f"If a person is present, use a: {entry.boy_or_girl}\n\n"
        f"{_photorealistic_hint()}\n"
    )


def build_stage3_prompt(entry: Entry, old_prompt: str, challenges: str, recommendations: str) -> str:
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
        f"{_photorealistic_hint()}\n"
    )
