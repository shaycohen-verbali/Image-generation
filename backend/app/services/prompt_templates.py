from __future__ import annotations

from app.models import Entry


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

DEFAULT_PHOTOREALISTIC_STYLE_ID = "aac_clean_photorealistic_v1"
DEFAULT_PHOTOREALISTIC_STYLE_NAME = "AAC Clean Photorealistic Style v1"
DEFAULT_PHOTOREALISTIC_STYLE_PROMPT_BLOCK = (
    "House visual style: AAC Clean Photorealistic Style v1. Create a clean, premium photorealistic image with one "
    "clear focal subject or one clear real-world concept. The image must be immediately recognizable to a child at a "
    "glance, with realistic materials, realistic lighting, clean edges, simple composition, and minimal distractors. "
    "Use bright natural color, medium contrast, and a supportive but unobtrusive background only when context helps "
    "recognition. Keep the subject large, centered or compositionally dominant, and easy to decode for AAC users and "
    "early learners. Avoid illustration, watercolor, cartoon anatomy, stylized facial features, clutter, text, "
    "watermark, dramatic shadows, moody lighting, excessive props, and unnecessary people."
)

DEFAULT_ILLUSTRATION_ENFORCED_PROMPT_TEMPLATE = (
    "Create an illustration for the AAC concept \"{word}\".\n"
    "Use this concept guidance from the prompt engineer: {source_prompt}\n"
    "Context: {context}\n"
    "Part of speech: {part_of_sentence}\n"
    "Category: {category}\n"
    "Hard requirements:\n"
    "- Resolved render style: illustration ({render_style_name}).\n"
    "- {person_decision_instruction}\n"
    "- Keep one clear focal subject and one clear action or concept.\n"
    "- Keep the person central, readable, and emotionally clear when present.\n"
    "- Do not add text.\n"
    "- Follow this illustration style block exactly:\n"
    "{render_style_block}\n"
)

DEFAULT_PHOTOREALISTIC_ENFORCED_PROMPT_TEMPLATE = (
    "Create a photorealistic AAC image for the concept \"{word}\".\n"
    "Use this concept guidance from the prompt engineer: {source_prompt}\n"
    "Context: {context}\n"
    "Part of speech: {part_of_sentence}\n"
    "Category: {category}\n"
    "Hard requirements:\n"
    "- Resolved render style: photorealistic ({render_style_name}).\n"
    "- {person_decision_instruction}\n"
    "- Show one clear focal subject or one clear real-world concept.\n"
    "- Keep the subject large, immediately recognizable, and free of distracting props.\n"
    "- Use realistic materials, realistic lighting, clean edges, and simple composition.\n"
    "- Do not add text.\n"
    "- Follow this photorealistic style block exactly:\n"
    "{render_style_block}\n"
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
    "Decision rule:\n"
    "- If a person is needed for AAC clarity, the prompt should use an illustration and make the person central.\n"
    "- If a person is not needed for AAC clarity, the prompt should be photorealistic and should not include a person.\n\n"
    "Illustration style to use when a person is needed ({visual_style_name} / {visual_style_id}):\n"
    "{visual_style_block}\n\n"
    "Photorealistic style to use when a person is not needed ({photorealistic_style_name} / {photorealistic_style_id}):\n"
    "{photorealistic_style_block}\n"
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
    "Current decision from the system: {resolved_need_person_reasoning}\n"
    "Resolved person-needed decision: {resolved_need_person}\n"
    "Resolved render style: {render_style_mode}\n"
    "{person_decision_instruction}\n\n"
    "Do not use text in the image.\n"
    "The word's category can add information in addition to its PoS.\n"
    "If render style is illustration, keep the exact illustration house style consistent with previous and future images.\n"
    "Illustration style to use when a person is needed ({visual_style_name} / {visual_style_id}):\n"
    "{visual_style_block}\n\n"
    "Photorealistic style to use when a person is not needed ({photorealistic_style_name} / {photorealistic_style_id}):\n"
    "{photorealistic_style_block}\n"
)


def _render_template(template_text: str, values: dict[str, str]) -> str:
    rendered = template_text
    for key, value in values.items():
        rendered = rendered.replace(f"{{{key}}}", str(value or ""))
    return rendered


def _strip_style_conflicts(prompt_text: str, render_style_mode: str) -> str:
    text = " ".join(str(prompt_text or "").split())
    if render_style_mode == "photorealistic":
        for marker in [
            "illustration",
            "storybook",
            "watercolor-gouache",
            "watercolor",
            "colored pencil",
            "cartoonish",
            "cartoon",
            "playful and kid-friendly",
            "kid-friendly",
        ]:
            text = text.replace(marker, "")
            text = text.replace(marker.title(), "")
    else:
        for marker in [
            "photorealistic",
            "realistic materials",
            "realistic lighting",
            "clean premium photorealistic",
        ]:
            text = text.replace(marker, "")
            text = text.replace(marker.title(), "")
    return " ".join(text.split()).strip()


def _render_with_visual_style(
    template_text: str,
    values: dict[str, str],
    *,
    visual_style_name: str,
    visual_style_id: str,
    visual_style_block: str,
) -> str:
    rendered = _render_template(template_text, values)
    if "{visual_style_block}" in template_text or "{photorealistic_style_block}" in template_text:
        return rendered
    if not visual_style_block.strip():
        return rendered
    style_section = (
        f"\nIllustration style to use when a person is needed ({visual_style_name} / {visual_style_id}):\n"
        f"{visual_style_block}\n\n"
        f"Photorealistic style to use when a person is not needed ({DEFAULT_PHOTOREALISTIC_STYLE_NAME} / {DEFAULT_PHOTOREALISTIC_STYLE_ID}):\n"
        f"{DEFAULT_PHOTOREALISTIC_STYLE_PROMPT_BLOCK}\n"
    )
    return f"{rendered.rstrip()}\n{style_section}"


def normalize_need_person(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    return "yes" if normalized == "yes" else "no"


def render_style_mode_for_need_person(need_person: str | None) -> str:
    return "illustration" if normalize_need_person(need_person) == "yes" else "photorealistic"


def person_instruction_for_need_person(need_person: str | None, *, boy_or_girl: str = "") -> str:
    normalized = normalize_need_person(need_person)
    if normalized == "yes":
        preferred_person = str(boy_or_girl or "").strip()
        if preferred_person:
            return (
                f"A person is required for clarity. Include one clear central person and use a {preferred_person}. "
                "Do not let extra people or background activity compete with the main meaning."
            )
        return "A person is required for clarity. Include one clear central person and avoid extra distracting people."
    return "A person is not required for clarity. Do not include any person in the image."


def style_block_for_render_mode(
    render_style_mode: str,
    *,
    illustration_style_id: str,
    illustration_style_name: str,
    illustration_style_block: str,
) -> dict[str, str]:
    if render_style_mode == "illustration":
        return {
            "render_style_mode": "illustration",
            "render_style_id": illustration_style_id,
            "render_style_name": illustration_style_name,
            "render_style_block": illustration_style_block,
        }
    return {
        "render_style_mode": "photorealistic",
        "render_style_id": DEFAULT_PHOTOREALISTIC_STYLE_ID,
        "render_style_name": DEFAULT_PHOTOREALISTIC_STYLE_NAME,
        "render_style_block": DEFAULT_PHOTOREALISTIC_STYLE_PROMPT_BLOCK,
    }


def resolve_person_decision(
    *,
    initial_need_person: str | None,
    person_needed_for_clarity: str | None = None,
    person_presence_problem: str | None = None,
    boy_or_girl: str = "",
    illustration_style_id: str = DEFAULT_VISUAL_STYLE_ID,
    illustration_style_name: str = DEFAULT_VISUAL_STYLE_NAME,
    illustration_style_block: str = DEFAULT_VISUAL_STYLE_PROMPT_BLOCK,
) -> dict[str, str]:
    initial = normalize_need_person(initial_need_person)
    validated = normalize_need_person(person_needed_for_clarity) if person_needed_for_clarity else initial
    presence_problem = str(person_presence_problem or "").strip().lower()
    resolved = validated
    reason = "kept_stage1_person_decision"

    if presence_problem == "missing_person":
        resolved = "yes"
        reason = "critique_required_person_after_missing_person"
    elif presence_problem == "unnecessary_person":
        resolved = "no"
        reason = "critique_removed_unnecessary_person"
    elif person_needed_for_clarity:
        resolved = validated
        reason = "critique_validated_person_need"

    render_style = render_style_mode_for_need_person(resolved)
    style = style_block_for_render_mode(
        render_style,
        illustration_style_id=illustration_style_id,
        illustration_style_name=illustration_style_name,
        illustration_style_block=illustration_style_block,
    )
    return {
        "initial_need_person": initial,
        "person_needed_for_clarity": validated,
        "person_presence_problem": presence_problem or "none",
        "resolved_need_person": resolved,
        "resolved_need_person_reasoning": reason,
        "person_decision_instruction": person_instruction_for_need_person(resolved, boy_or_girl=boy_or_girl),
        **style,
    }


def apply_render_decision_to_prompt(
    prompt_text: str,
    *,
    resolved_need_person: str,
    resolved_need_person_reasoning: str = "kept_stage1_person_decision",
    word: str = "",
    part_of_sentence: str = "",
    category: str = "",
    context: str = "",
    boy_or_girl: str = "",
    illustration_style_id: str = DEFAULT_VISUAL_STYLE_ID,
    illustration_style_name: str = DEFAULT_VISUAL_STYLE_NAME,
    illustration_style_block: str = DEFAULT_VISUAL_STYLE_PROMPT_BLOCK,
) -> tuple[str, dict[str, str]]:
    normalized_need_person = normalize_need_person(resolved_need_person)
    render_style_mode = render_style_mode_for_need_person(normalized_need_person)
    style = style_block_for_render_mode(
        render_style_mode,
        illustration_style_id=illustration_style_id,
        illustration_style_name=illustration_style_name,
        illustration_style_block=illustration_style_block,
    )
    decision = {
        "initial_need_person": normalized_need_person,
        "person_needed_for_clarity": normalized_need_person,
        "person_presence_problem": "none",
        "resolved_need_person": normalized_need_person,
        "resolved_need_person_reasoning": resolved_need_person_reasoning,
        "person_decision_instruction": person_instruction_for_need_person(normalized_need_person, boy_or_girl=boy_or_girl),
        **style,
    }
    normalized_source_prompt = _strip_style_conflicts(prompt_text, decision["render_style_mode"]) or str(prompt_text or "").strip()
    enforced_prompt = _render_template(
        DEFAULT_ILLUSTRATION_ENFORCED_PROMPT_TEMPLATE
        if decision["render_style_mode"] == "illustration"
        else DEFAULT_PHOTOREALISTIC_ENFORCED_PROMPT_TEMPLATE,
        {
            "word": word,
            "context": context,
            "part_of_sentence": part_of_sentence,
            "category": category,
            "source_prompt": normalized_source_prompt,
            "render_style_name": decision["render_style_name"],
            "person_decision_instruction": decision["person_decision_instruction"],
            "render_style_block": decision["render_style_block"],
        },
    ).strip()
    return enforced_prompt, decision


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
            "photorealistic_hint": _photorealistic_hint(),
            "visual_style_id": visual_style_id,
            "visual_style_name": visual_style_name,
            "visual_style_block": visual_style_block,
            "photorealistic_style_id": DEFAULT_PHOTOREALISTIC_STYLE_ID,
            "photorealistic_style_name": DEFAULT_PHOTOREALISTIC_STYLE_NAME,
            "photorealistic_style_block": DEFAULT_PHOTOREALISTIC_STYLE_PROMPT_BLOCK,
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
    resolved_need_person: str = "no",
    resolved_need_person_reasoning: str = "kept_stage1_person_decision",
    render_style_mode: str = "photorealistic",
    person_decision_instruction: str = "A person is not required for clarity. Do not include any person in the image.",
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
            "photorealistic_hint": _photorealistic_hint(),
            "visual_style_id": visual_style_id,
            "visual_style_name": visual_style_name,
            "visual_style_block": visual_style_block,
            "photorealistic_style_id": DEFAULT_PHOTOREALISTIC_STYLE_ID,
            "photorealistic_style_name": DEFAULT_PHOTOREALISTIC_STYLE_NAME,
            "photorealistic_style_block": DEFAULT_PHOTOREALISTIC_STYLE_PROMPT_BLOCK,
            "resolved_need_person": normalize_need_person(resolved_need_person),
            "resolved_need_person_reasoning": resolved_need_person_reasoning,
            "render_style_mode": render_style_mode,
            "person_decision_instruction": person_decision_instruction,
        },
        visual_style_id=visual_style_id,
        visual_style_name=visual_style_name,
        visual_style_block=visual_style_block,
    )
