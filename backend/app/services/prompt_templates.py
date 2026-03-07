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


DEFAULT_STAGE1_PROMPT_TEMPLATE = (
    "Task: Create the first image prompt for the given word and decide if the prompt needs a person.\n"
    "Return STRICT JSON with keys exactly:\n"
    '{ "first prompt": "<string>", "need a person": "yes" | "no" }\n\n'
    "Context: {context}\n"
    "Word: {word}\n"
    "Part of speech: {part_of_sentence}\n"
    "Category: {category}\n"
    "If a person is present, use a: {boy_or_girl}\n\n"
    "{photorealistic_hint}\n"
)


DEFAULT_STAGE3_PROMPT_TEMPLATE = (
    "Create an upgraded image prompt for the given word. Return STRICT JSON:\n"
    '{ "upgraded prompt": "<string>" }\n\n'
    "context for the image: {context}\n"
    "Old prompt: {old_prompt}\n"
    "challenges and improvements with the old image: challenges={challenges}; recommendations={recommendations}\n"
    "word: {word}\n"
    "part of sentence: {part_of_sentence}\n"
    "Category: {category}\n"
    "If a person is present, use a {boy_or_girl} as the person.\n\n"
    "Do not use text in the image.\n"
    "The word's category can add information in addition to its PoS.\n"
    "{photorealistic_hint}\n"
)


def _render_template(template_text: str, values: dict[str, str]) -> str:
    rendered = template_text
    for key, value in values.items():
        rendered = rendered.replace(f"{{{key}}}", str(value or ""))
    return rendered


def build_stage1_prompt(entry: Entry, template_text: str | None = None) -> str:
    return _render_template(
        template_text or DEFAULT_STAGE1_PROMPT_TEMPLATE,
        {
            "context": entry.context,
            "word": entry.word,
            "part_of_sentence": entry.part_of_sentence,
            "category": entry.category,
            "boy_or_girl": entry.boy_or_girl,
            "photorealistic_hint": _photorealistic_hint(),
        },
    )


def build_stage3_prompt(
    entry: Entry,
    old_prompt: str,
    challenges: str,
    recommendations: str,
    template_text: str | None = None,
) -> str:
    return _render_template(
        template_text or DEFAULT_STAGE3_PROMPT_TEMPLATE,
        {
            "context": entry.context,
            "old_prompt": old_prompt,
            "challenges": challenges,
            "recommendations": recommendations,
            "word": entry.word,
            "part_of_sentence": entry.part_of_sentence,
            "category": entry.category,
            "boy_or_girl": entry.boy_or_girl,
            "photorealistic_hint": _photorealistic_hint(),
        },
    )
