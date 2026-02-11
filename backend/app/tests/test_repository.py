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


def test_create_runs_clamps_quality_threshold_to_95(db_session) -> None:
    repo = Repository(db_session)
    entry = repo.create_entry(
        {
            "word": "apple",
            "part_of_sentence": "noun",
            "category": "food",
            "context": "fruit",
            "boy_or_girl": "girl",
            "batch": "1",
        }
    )
    run = repo.create_runs([entry.id], quality_threshold=90, max_optimization_attempts=3)[0]
    assert run.quality_threshold == 95


def test_update_runtime_config_clamps_quality_threshold_to_95(db_session) -> None:
    repo = Repository(db_session)
    config = repo.update_runtime_config({"quality_threshold": 90})
    assert config.quality_threshold == 95
