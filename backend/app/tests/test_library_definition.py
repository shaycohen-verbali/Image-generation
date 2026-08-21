from app.services.library import _sense_definition


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
