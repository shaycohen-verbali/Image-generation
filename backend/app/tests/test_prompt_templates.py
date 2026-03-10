from app.models import Entry
from app.services.prompt_templates import (
    DEFAULT_PHOTOREALISTIC_STYLE_NAME,
    DEFAULT_VISUAL_STYLE_ID,
    DEFAULT_VISUAL_STYLE_NAME,
    apply_render_decision_to_prompt,
    build_stage1_prompt,
    build_stage3_prompt,
    resolve_person_decision,
)


def make_entry() -> Entry:
    return Entry(
        id="ent_test",
        word="bucket",
        part_of_sentence="noun",
        category="food",
        context="at the beach",
        boy_or_girl="girl",
        person_gender_options_json='["male","female"]',
        person_age_options_json='["kid","toddler"]',
        person_skin_color_options_json='["white","brown"]',
        batch="",
        source_row_hash="hash",
    )


def test_stage1_prompt_includes_both_style_paths() -> None:
    prompt = build_stage1_prompt(make_entry())
    assert DEFAULT_VISUAL_STYLE_NAME in prompt
    assert DEFAULT_VISUAL_STYLE_ID in prompt
    assert DEFAULT_PHOTOREALISTIC_STYLE_NAME in prompt
    assert "If a person is needed for AAC clarity" in prompt


def test_stage3_prompt_appends_visual_style_even_when_template_has_no_placeholder() -> None:
    prompt = build_stage3_prompt(
        make_entry(),
        old_prompt="old prompt",
        challenges="too cluttered",
        recommendations="make bucket larger",
        template_text="Word: {word}\nOld prompt: {old_prompt}",
        resolved_need_person="yes",
        resolved_need_person_reasoning="critique_required_person_after_missing_person",
        render_style_mode="illustration",
        person_decision_instruction="A person is required for clarity.",
    )
    assert "Word: bucket" in prompt
    assert "Old prompt: old prompt" in prompt
    assert DEFAULT_VISUAL_STYLE_NAME in prompt


def test_resolve_person_decision_can_override_stage1_hypothesis() -> None:
    decision = resolve_person_decision(
        initial_need_person="no",
        person_needed_for_clarity="yes",
        person_presence_problem="missing_person",
        person_profile="male, kid (5-9), White skin",
    )
    assert decision["resolved_need_person"] == "yes"
    assert decision["render_style_mode"] == "illustration"
    assert decision["resolved_need_person_reasoning"] == "critique_required_person_after_missing_person"


def test_apply_render_decision_to_prompt_enforces_photorealistic_without_person() -> None:
    enforced_prompt, decision = apply_render_decision_to_prompt(
        "A bright colored pencil illustration of a red bucket on grass.",
        resolved_need_person="no",
        word="bucket",
        part_of_sentence="noun",
        category="",
        context="at the beach",
        person_profile="male, kid (5-9), White skin",
    )
    assert decision["render_style_mode"] == "photorealistic"
    assert "Do not include any person" in enforced_prompt
    assert DEFAULT_PHOTOREALISTIC_STYLE_NAME in enforced_prompt
    assert "Create a photorealistic AAC image" in enforced_prompt
    assert "colored pencil" not in enforced_prompt.lower()


def test_apply_render_decision_to_prompt_enforces_illustration_with_person() -> None:
    enforced_prompt, decision = apply_render_decision_to_prompt(
        "A clean photorealistic bucket with realistic lighting.",
        resolved_need_person="yes",
        word="carry",
        part_of_sentence="verb",
        category="",
        context="at the beach",
        person_profile="male, kid (5-9), White skin",
    )
    assert decision["render_style_mode"] == "illustration"
    assert "Create an illustration for the AAC concept" in enforced_prompt
    assert "Include one clear central person" in enforced_prompt
    assert "Follow this illustration style block exactly" in enforced_prompt
