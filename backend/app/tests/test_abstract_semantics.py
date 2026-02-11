from app.services.abstract_semantics import detect_abstract_intent


def test_detect_abstract_intent_classifies_none() -> None:
    intent = detect_abstract_intent(
        word="none",
        part_of_sentence="pronoun",
        context="there are no apples in the basket",
        category="food",
    )
    assert intent.is_abstract is True
    assert "lexicon_match" in intent.reason_codes
    assert intent.contrast_subject in {"apples", "food"}


def test_detect_abstract_intent_keeps_concrete_noun_non_abstract() -> None:
    intent = detect_abstract_intent(
        word="apple",
        part_of_sentence="noun",
        context="a red apple on a white plate",
        category="food",
    )
    assert intent.is_abstract is False
    assert intent.reason_codes == []
