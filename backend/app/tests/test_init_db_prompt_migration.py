from app.db.init_db import _ensure_word_meaning_prompt_fields


def test_word_meaning_migration_renames_placeholder_and_repositions_synonyms() -> None:
    migrated = _ensure_word_meaning_prompt_fields(
        "Word: {word}\n"
        "Category: {category}\n"
        "Decision rule: keep it clear\n"
        "Word synonyms for better meaning: {word_synonyms_for_better_meaning}\n"
    )

    assert "Word sense: {word_sense}" in migrated
    assert "{category}" not in migrated
    assert migrated.index("Word sense: {word_sense}") < migrated.index(
        "Word synonyms for better meaning: {word_synonyms_for_better_meaning}"
    ) < migrated.index("Decision rule: keep it clear")


def test_word_meaning_migration_is_idempotent() -> None:
    template = (
        "Word sense: {word_sense}\n"
        "Word synonyms for better meaning: {word_synonyms_for_better_meaning}\n"
    )

    assert _ensure_word_meaning_prompt_fields(_ensure_word_meaning_prompt_fields(template)) == template
