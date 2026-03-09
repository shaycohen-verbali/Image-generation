from app.models import Entry
from app.services.prompt_templates import (
    DEFAULT_VISUAL_STYLE_ID,
    DEFAULT_VISUAL_STYLE_NAME,
    build_stage1_prompt,
    build_stage3_prompt,
)


def make_entry() -> Entry:
    return Entry(
        id="ent_test",
        word="bucket",
        part_of_sentence="noun",
        category="food",
        context="at the beach",
        boy_or_girl="girl",
        batch="",
        source_row_hash="hash",
    )


def test_stage1_prompt_includes_visual_style_and_overrides_photorealistic_rule() -> None:
    prompt = build_stage1_prompt(make_entry())
    assert DEFAULT_VISUAL_STYLE_NAME in prompt
    assert DEFAULT_VISUAL_STYLE_ID in prompt
    assert "Do not switch to photorealistic rendering based on category" in prompt


def test_stage3_prompt_appends_visual_style_even_when_template_has_no_placeholder() -> None:
    prompt = build_stage3_prompt(
        make_entry(),
        old_prompt="old prompt",
        challenges="too cluttered",
        recommendations="make bucket larger",
        template_text="Word: {word}\nOld prompt: {old_prompt}",
    )
    assert "Word: bucket" in prompt
    assert "Old prompt: old prompt" in prompt
    assert DEFAULT_VISUAL_STYLE_NAME in prompt
