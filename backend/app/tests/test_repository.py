from app.services.repository import Repository


def test_unique_word_pos_category_returns_same_entry(db_session) -> None:
    repo = Repository(db_session)
    payload = {
        "word": "run",
        "part_of_sentence": "verb",
        "category": "actions",
        "context": "movement",
        "boy_or_girl": "boy",
        "batch": "1",
    }

    first = repo.create_entry(payload)
    second = repo.create_entry(payload)

    assert first.id == second.id
    assert first.word == "run"
