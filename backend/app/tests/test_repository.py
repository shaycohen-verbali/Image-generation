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


def test_update_runtime_config_clamps_worker_count(db_session) -> None:
    repo = Repository(db_session)
    config_low = repo.update_runtime_config({"max_parallel_runs": 0})
    assert config_low.max_parallel_runs == 1

    config_high = repo.update_runtime_config({"max_parallel_runs": 999})
    assert config_high.max_parallel_runs == 50


def test_update_runtime_config_normalizes_model_fields(db_session) -> None:
    repo = Repository(db_session)
    config = repo.update_runtime_config(
        {
            "stage3_critique_model": "gpt-40-mini",
            "stage3_generate_model": "bad-model-name",
            "quality_gate_model": "gemini-3-pro",
            "prompt_engineer_mode": "not-real",
            "responses_prompt_engineer_model": "not-a-real-model",
            "stage1_prompt_template": "",
            "stage3_prompt_template": "",
        }
    )
    assert config.stage3_critique_model == "gpt-4o-mini"
    assert config.stage3_generate_model == "nano-banana-2"
    assert config.quality_gate_model == "gemini-3-pro"
    assert config.prompt_engineer_mode == "responses_api"
    assert config.responses_prompt_engineer_model == "gpt-5.4"
    assert config.stage1_prompt_template
    assert config.stage3_prompt_template
    assert config.visual_style_id
    assert config.visual_style_name
    assert config.visual_style_prompt_block


def test_add_asset_is_idempotent_by_run_stage_attempt_and_file_name(db_session) -> None:
    repo = Repository(db_session)
    entry = repo.create_entry(
        {
            "word": "soccer",
            "part_of_sentence": "verb",
            "category": "",
            "context": "",
            "boy_or_girl": "male",
            "batch": "1",
        }
    )
    run = repo.create_runs([entry.id], quality_threshold=95, max_optimization_attempts=3)[0]
    first = repo.add_asset(
        run_id=run.id,
        stage_name="stage4_variant_generate",
        attempt=1,
        file_name="stage4_variant_soccer_male_kid_white_attempt_1.jpg",
        abs_path="/tmp/first.jpg",
        mime_type="image/jpeg",
        sha256="abc",
        width=1200,
        height=896,
        origin_url="https://example.com/first.jpg",
        model_name="google/nano-banana-2",
    )
    second = repo.add_asset(
        run_id=run.id,
        stage_name="stage4_variant_generate",
        attempt=1,
        file_name="stage4_variant_soccer_male_kid_white_attempt_1.jpg",
        abs_path="/tmp/second.jpg",
        mime_type="image/jpeg",
        sha256="def",
        width=1200,
        height=896,
        origin_url="https://example.com/second.jpg",
        model_name="google/nano-banana-2",
    )
    assert first.id == second.id
    assert second.abs_path == "/tmp/second.jpg"
