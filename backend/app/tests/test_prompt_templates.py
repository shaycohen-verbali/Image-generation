from app.models import Entry
from app.services.prompt_templates import build_stage1_prompt


def test_stage1_prompt_includes_abstract_contrast_instructions() -> None:
    entry = Entry(
        id="ent_1",
        word="none",
        part_of_sentence="pronoun",
        category="",
        context="none of the apples are on the plate",
        boy_or_girl="girl",
        batch="1",
        source_row_hash="hash",
    )
    prompt = build_stage1_prompt(
        entry,
        abstract_intent={
            "is_abstract": True,
            "contrast_subject": "apples",
            "contrast_pattern": "single_frame_contrast",
            "reason_codes": ["lexicon_match"],
        },
    )
    assert "single-frame contrast composition" in prompt
    assert "Focus the contrast on this subject: apples" in prompt
