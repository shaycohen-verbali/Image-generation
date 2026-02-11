from app.services.csv_service import parse_entries_csv, validate_entry_row


def test_parse_entries_csv_supports_existing_column_names() -> None:
    content = (
        'word,part of sentence,category,context,boy or girl,batch\n'
        'apple,noun,food,fruit,girl,2\n'
    ).encode('utf-8')

    rows = parse_entries_csv(content)

    assert rows[0]["word"] == "apple"
    assert rows[0]["part_of_sentence"] == "noun"
    assert rows[0]["category"] == "food"


def test_validate_entry_row_requires_word_and_part_of_sentence() -> None:
    error = validate_entry_row({"word": "", "part_of_sentence": "noun", "category": "food"})
    assert error is not None


def test_validate_entry_row_allows_empty_category() -> None:
    error = validate_entry_row({"word": "apple", "part_of_sentence": "noun", "category": ""})
    assert error is None
