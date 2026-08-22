from app.services.library import _image_source_metadata, _sense_definition


def test_sense_definition_uses_authoritative_inventory_category() -> None:
    row = {
        "category": "a church associated with a monastery or convent",
        "sense_wordnet": "a church associated with a monastery or convent",
        "sense_oxford": "a large church together with buildings where monks or nuns live",
    }

    assert _sense_definition(row) == "a church associated with a monastery or convent"


def test_sense_definition_falls_back_in_generation_order() -> None:
    row = {
        "category": "",
        "sense_wordnet": "wordnet definition",
        "sense_oxford": "oxford definition",
    }

    assert _sense_definition(row) == "wordnet definition"


def test_image_source_metadata_exposes_canonical_word_and_sense() -> None:
    metadata = _image_source_metadata(
        requested_word="abc",
        sense_id="a6fde4497c2d81b3",
        canonical_words={"alphabet"},
        canonical_sense_ids={"04014442ee4c9d01"},
    )

    assert metadata == {
        "canonical_word": "alphabet",
        "canonical_sense_id": "04014442ee4c9d01",
        "uses_canonical_image": True,
    }


def test_image_source_metadata_marks_direct_inventory_source() -> None:
    metadata = _image_source_metadata(
        requested_word="abc",
        sense_id="4b2f8e385e80ad90",
        canonical_words=set(),
        canonical_sense_ids=set(),
    )

    assert metadata["canonical_word"] == "abc"
    assert metadata["canonical_sense_id"] == "4b2f8e385e80ad90"
    assert metadata["uses_canonical_image"] is False
