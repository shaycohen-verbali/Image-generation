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


DEFAULT_VISUAL_STYLE_ID = "warm_watercolor_storybook_kids_v3"
DEFAULT_VISUAL_STYLE_NAME = "Warm Watercolor Storybook Kids Style v3"
DEFAULT_VISUAL_STYLE_PROMPT_BLOCK = (
    "House visual style: Warm Watercolor Storybook Kids Style v3. Create a premium child-friendly "
    "storybook illustration with watercolor-gouache softness and a polished picture-book finish. "
    "The image must feel warm, safe, playful, vivid, inviting, emotionally legible, and easy for AAC users "
    "and early learners to understand at a glance. Keep one clear focal subject and one clear action or concept, "
    "with a crisp polished focal subject, stronger contrast, vivid color richness, bright cheerful colors, warm "
    "golden sunlight, lively natural tones, and a premium picture-book finish. Use simple supportive backgrounds "
    "that do not compete with the subject. If a child is present, use oversized expressive eyes, rosy cheeks, "
    "soft rounded childlike anatomy, clear friendly emotion, and a readable silhouette. Keep watercolor-gouache "
    "softness, clean polished outlines, warm storybook lighting, and polished form rendering. Do not make the "
    "image faded, washed out, overly pale, muddy, flat, dark, creepy, cluttered, sketchy, photorealistic, "
    "realistic-anatomy driven, 3D rendered, generic flashcard-like, or text-based. This house style overrides "
    "category-based photorealistic rendering."
)


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
    "Visual style to apply consistently across all images and attempts ({visual_style_name} / {visual_style_id}):\n"
    "{visual_style_block}\n"
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
    "Keep the exact house visual style consistent with previous and future images.\n"
    "Visual style to apply consistently across all images and attempts ({visual_style_name} / {visual_style_id}):\n"
    "{visual_style_block}\n"
)


def _render_template(template_text: str, values: dict[str, str]) -> str:
    rendered = template_text
    for key, value in values.items():
        rendered = rendered.replace(f"{{{key}}}", str(value or ""))
    return rendered


def _render_with_visual_style(
    template_text: str,
    values: dict[str, str],
    *,
    visual_style_name: str,
    visual_style_id: str,
    visual_style_block: str,
) -> str:
    rendered = _render_template(template_text, values)
    if not visual_style_block.strip():
        return rendered
    if "{visual_style_block}" in template_text:
        return rendered
    style_section = (
        f"\nVisual style to apply consistently across all images and attempts ({visual_style_name} / {visual_style_id}):\n"
        f"{visual_style_block}\n"
    )
    return f"{rendered.rstrip()}\n{style_section}"


def build_stage1_prompt(
    entry: Entry,
    template_text: str | None = None,
    *,
    visual_style_id: str = DEFAULT_VISUAL_STYLE_ID,
    visual_style_name: str = DEFAULT_VISUAL_STYLE_NAME,
    visual_style_block: str = DEFAULT_VISUAL_STYLE_PROMPT_BLOCK,
) -> str:
    return _render_with_visual_style(
        template_text or DEFAULT_STAGE1_PROMPT_TEMPLATE,
        {
            "context": entry.context,
            "word": entry.word,
            "part_of_sentence": entry.part_of_sentence,
            "category": entry.category,
            "boy_or_girl": entry.boy_or_girl,
            "photorealistic_hint": (
                "Do not switch to photorealistic rendering based on category. Follow the visual style block below."
                if visual_style_block.strip()
                else _photorealistic_hint()
            ),
            "visual_style_id": visual_style_id,
            "visual_style_name": visual_style_name,
            "visual_style_block": visual_style_block,
        },
        visual_style_id=visual_style_id,
        visual_style_name=visual_style_name,
        visual_style_block=visual_style_block,
    )


def build_stage3_prompt(
    entry: Entry,
    old_prompt: str,
    challenges: str,
    recommendations: str,
    template_text: str | None = None,
    *,
    visual_style_id: str = DEFAULT_VISUAL_STYLE_ID,
    visual_style_name: str = DEFAULT_VISUAL_STYLE_NAME,
    visual_style_block: str = DEFAULT_VISUAL_STYLE_PROMPT_BLOCK,
) -> str:
    return _render_with_visual_style(
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
            "photorealistic_hint": (
                "Do not switch to photorealistic rendering based on category. Follow the visual style block below."
                if visual_style_block.strip()
                else _photorealistic_hint()
            ),
            "visual_style_id": visual_style_id,
            "visual_style_name": visual_style_name,
            "visual_style_block": visual_style_block,
        },
        visual_style_id=visual_style_id,
        visual_style_name=visual_style_name,
        visual_style_block=visual_style_block,
    )
