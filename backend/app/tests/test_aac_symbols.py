from pathlib import Path
from types import SimpleNamespace

import pytest

from app.inventory_models import word_inventory
from app.services.aac_symbols import SYMBOL_PROFILE, selected_output_profiles, symbol_reference_path
from app.services.csv_dag_service import CsvDagService, _dependency_profile_for
from app.services.google_image_client import GoogleImageClient
from app.services.inventory_sync import InventorySyncService
from app.services.person_profiles import planned_review_profiles
from app.services.pipeline import PipelineRunner
from app.services.repository import Repository


def test_symbols_are_one_additional_profile_even_with_all_demographics():
    profiles = selected_output_profiles(
        ["male", "female"], ["kid", "toddler", "tween", "teenager", "symbol"],
        ["white", "black", "asian", "brown"],
    )
    assert len(profiles) == 33
    assert [p for p in profiles if p["age"] == "symbol"] == [SYMBOL_PROFILE]
    entry = SimpleNamespace(
        person_gender_options_json='["male","female"]',
        person_age_options_json='["kid","toddler","tween","teenager","symbol"]',
        person_skin_color_options_json='["white","black","asian","brown"]',
    )
    assert planned_review_profiles(entry)[-1] == SYMBOL_PROFILE
    assert len(planned_review_profiles(entry)) == 17
    assert _dependency_profile_for(SYMBOL_PROFILE) == {"gender": "male", "age": "kid", "skin_color": "white"}
    assert PipelineRunner._should_run_variant_review(SYMBOL_PROFILE, _dependency_profile_for(SYMBOL_PROFILE)) is False


def test_symbol_slots_do_not_overlap_demographic_images(db_session):
    assert "kid_male_white_regular_path" in word_inventory.c
    assert "symbol_male_white_regular_path" in word_inventory.c
    assert "symbol_female_black_regular_path" not in word_inventory.c
    repo = Repository(db_session)
    job = repo.create_csv_job(batch_id="symbols", source_file_name="test.csv", execution_mode="csv_dag", config_snapshot={
        "person_gender_options": ["female"], "person_age_options": ["kid", "symbol"],
        "person_skin_color_options": ["black"],
    })
    expected = InventorySyncService(db_session)._expected_slot_names(job)
    assert set(expected) == {"kid_female_black_regular_path", "kid_female_black_white_bg_path",
                            "symbol_male_white_regular_path", "symbol_male_white_white_bg_path"}


def test_existing_inventory_gets_only_the_new_symbol_slots(monkeypatch):
    from sqlalchemy import create_engine, text
    from app.db.init_db import _ensure_inventory_columns
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE word_inventory (id TEXT PRIMARY KEY)"))
    monkeypatch.setattr("app.db.inventory_session.inventory_engine", engine)
    _ensure_inventory_columns()
    _ensure_inventory_columns()  # Startup migration is safe to repeat.
    with engine.connect() as conn:
        names = {row[1] for row in conn.execute(text("PRAGMA table_info(word_inventory)"))}
    assert {name for name in names if name.startswith("symbol_")} == {
        "symbol_male_white_regular_path", "symbol_male_white_white_bg_path",
        "symbol_male_white_regular_prompt", "symbol_male_white_white_bg_prompt",
    }


def test_symbol_only_task_plan_creates_white_boy_dependency(db_session):
    repo = Repository(db_session)
    service = CsvDagService(db_session)
    entry = repo.create_entry({"word": "apple", "part_of_sentence": "noun", "category": "fruit"})
    job = repo.create_csv_job(batch_id="symbol_only", source_file_name="test.csv", execution_mode="csv_dag", config_snapshot={
        "person_gender_options": [], "person_age_options": ["symbol"], "person_skin_color_options": [],
    })
    item = repo.create_csv_job_item(csv_job_id=job.id, entry_id=entry.id, row_index=1, source_row={"word": "apple"})
    specs, skipped = service._build_task_specs(item, entry, job)
    assert not skipped
    assert [s["node"].profile_key for s in specs] == ["male:kid:white", "male:symbol:white"]
    assert specs[1]["node"].source_profile_key == "male:kid:white"
    assert specs[1]["node"].dependency_task_ids_json == f'["{specs[0]["node"].id}"]'


def test_symbol_request_contains_both_images_and_square_output(tmp_path, monkeypatch):
    client = GoogleImageClient()
    captured = {}
    try:
        def fake_generation(**kwargs):
            captured.update(kwargs)
            return {"status": "succeeded"}
        monkeypatch.setattr(client, "_run_generation", fake_generation)
        source = tmp_path / "white-boy.jpg"
        request = client.profile_variant_request_summary(
            source, word="accept; agree", profile_description="AAC symbols", target_age="symbol", aspect_ratio="4:3",
            model_choice="nano-banana-2",
        )
        assert request["style_version"] == "soft-signal-v1"
        assert request["aspect_ratio"] == "1:1"
        assert "accept; agree" in request["prompt"]
        assert "soccer ball" not in request["prompt"]
        assert "Change only the main person" not in request["prompt"]
        prediction = client.submit_nano_banana_profile_variant(
            source, run_id="test", word="apple", profile_description="AAC symbols", target_age="symbol",
            aspect_ratio="4:3", model_choice="nano-banana-2",
        )
        client._prediction_futures[prediction["id"]].result(timeout=5)
        assert captured["image_paths"] == [source, symbol_reference_path()]
        assert captured["aspect_ratio"] == "1:1"
    finally:
        client.close()


@pytest.mark.parametrize("immutable_assets", [False, True])
def test_single_concept_generates_symbols_even_when_no_person_is_needed(db_session, monkeypatch, immutable_assets):
    from app.core.config import get_settings
    from app.tests.test_pipeline_integration import MockGoogleImageClient, MockOpenAI, MockReplicate
    monkeypatch.setattr(get_settings(), "selected_winner_flow_enabled", immutable_assets)
    monkeypatch.setattr("app.services.pipeline.sleep", lambda seconds: None)
    repo = Repository(db_session)
    entry = repo.create_entry({
        "word": "apple", "part_of_sentence": "noun", "category": "fruit",
        "person_age_options": ["kid", "symbol"],
    })
    run = repo.create_runs([entry.id], quality_threshold=95, max_optimization_attempts=3)[0]
    google = MockGoogleImageClient()
    class SymbolReviewer(MockOpenAI):
        def _vision_json(self, *, prompt, **kwargs):
            if "reference_communicates_target" in prompt:
                return {"reference_communicates_target": True, "critical_components": [{"id": "C1", "description": "apple fruit"}]}, {}
            return {"symbol_communicates_target": True, "components": [{"id": "C1", "status": "present"}]}, {}
    runner = PipelineRunner(db_session, openai_client=SymbolReviewer(scores=[95]),
                            replicate_client=MockReplicate(), google_image_client=google)
    result = runner.process_run(run.id)
    assert result.status == "completed_pass", result.error_detail
    assert len(google.variant_submissions) == 1
    assert all(p["model_choice"] == "nano-banana-2" for p in google.variant_submissions)
    assert all("AAC symbols" in p["profile_description"] for p in google.variant_submissions)
    _, _, _, assets, _ = repo.run_details(run.id)
    assert any("symbol" in a.file_name and a.stage_name == "stage5_variant_white_bg" for a in assets)
    regular = next(a for a in assets if "symbol" in a.file_name and a.stage_name == "stage4_variant_generate")
    white = next(a for a in assets if "symbol" in a.file_name and a.stage_name == "stage5_variant_white_bg")
    assert Path(regular.abs_path).read_bytes() == Path(white.abs_path).read_bytes()
    assert white.source_asset_id == regular.id


@pytest.mark.parametrize("has_person", ["yes", "no"])
def test_matalk_export_keeps_symbol_identity_separate_from_originals(has_person):
    from app.services.matalk_export import build_matalk_tables
    tables = build_matalk_tables([{
        "word": "apple", "sense_id": "apple-fruit", "canonical_sense_id": "apple-fruit",
        "part_of_speech": "noun", "has_person": has_person,
        "kid_male_white_regular_path": "https://example.test/original.jpg",
        "symbol_male_white_regular_path": "https://example.test/symbol.jpg",
        "symbol_male_white_white_bg_path": "https://example.test/symbol-white.jpg",
    }])
    assert len(tables.image_rows) == 3
    symbols = [image for image in tables.image_rows if image["age"] == "symbol"]
    assert {image["image_url"] for image in symbols} == {
        "https://example.test/symbol.jpg", "https://example.test/symbol-white.jpg",
    }


def test_library_returns_symbol_images_and_prompts(monkeypatch):
    from sqlalchemy import create_engine
    from app.services import library
    monkeypatch.setattr(library, "inventory_engine", create_engine("sqlite:///:memory:"))
    row = {
        "word": "apple", "sense_id": "apple-fruit",
        "symbol_male_white_regular_path": "/images/apple-symbol.jpg",
        "symbol_male_white_regular_prompt": "Fixed AAC style conversion",
    }
    monkeypatch.setattr(library, "_inventory_detail_rows", lambda *args, **kwargs: [row])
    monkeypatch.setattr(library, "_lookup_rows_for_sense_ids", lambda *args: [])
    result = library.list_sense_images("apple-fruit")
    assert len(result["images"]) == 1
    image = result["images"][0]
    assert image["age"] == "symbol"
    assert image["path"] == row["symbol_male_white_regular_path"]
    assert image["prompt"] == row["symbol_male_white_regular_prompt"]


@pytest.mark.parametrize("results", [
    [],
    [{"id": "C1", "status": "missing"}],
    [{"id": "unknown", "status": "present"}],
    [{"id": "C1", "status": "present"}, {"id": "C1", "status": "present"}],
])
def test_symbol_meaning_check_rejects_missing_or_invalid_component_evidence(results):
    from app.services.aac_symbols import check_symbol_meaning
    responses = iter([
        {"reference_communicates_target": True, "critical_components": [{"id": "C1", "description": "apple"}]},
        {"symbol_communicates_target": True, "components": results},
    ])
    client = SimpleNamespace(_vision_json=lambda **kwargs: (next(responses), {}))
    result = check_symbol_meaning(client, Path("source.jpg"), Path("symbol.jpg"), meaning="apple", model="test")
    assert result["passed"] is False


@pytest.mark.parametrize("has_person", ["yes", "no"])
def test_symbols_run_for_objects_and_people_from_white_boy_base(db_session, monkeypatch, has_person):
    repo = Repository(db_session)
    service = CsvDagService(db_session)
    entry = repo.create_entry({"word": "apple", "part_of_sentence": "noun", "category": "fruit"})
    repo.update_entry_has_person(entry.id, has_person)
    run = repo.create_shadow_run(entry_id=entry.id, quality_threshold=95, max_optimization_attempts=3)
    job = repo.create_csv_job(batch_id=f"symbols_{has_person}", source_file_name="test.csv", execution_mode="csv_dag", config_snapshot={})
    item = repo.create_csv_job_item(csv_job_id=job.id, entry_id=entry.id, row_index=1, source_row={"word": "apple"})
    item = repo.update_csv_job_item(item, shadow_run_id=run.id)
    def asset(name, stage):
        return repo.add_asset(run_id=run.id, stage_name=stage, attempt=1, file_name=f"{name}.jpg",
                              abs_path=f"/tmp/{name}.jpg", mime_type="image/jpeg", sha256=name,
                              width=100, height=100, origin_url="", model_name="test")
    base = asset("white-boy-base", "stage4_white_bg")
    softened = asset("softened", "stage3_post_quality_accessibility_generate")
    symbol = asset("symbol", "stage4_variant_generate")
    repo.update_csv_job_item(item, base_regular_asset_id=base.id, base_white_bg_asset_id=base.id, base_soften_asset_id=softened.id)
    dep = repo.create_csv_task_node(csv_job_id=job.id, csv_job_item_id=item.id, step_name="step1_base", task_key="base",
        profile_key="male:kid:white", source_profile_key="", branch_role="base", dependency_keys=[], dependency_task_ids=[], status="completed")
    task = repo.create_csv_task_node(csv_job_id=job.id, csv_job_item_id=item.id, step_name="step2_variant", task_key="symbol",
        profile_key="male:symbol:white", source_profile_key="male:kid:white", branch_role="aac_symbol",
        dependency_keys=[dep.task_key], dependency_task_ids=[dep.id], status="running")
    captured = {}
    class FakeRunner:
        def __init__(self, db):
            self.google_images = SimpleNamespace(close=lambda: None)
        def create_profile_variant_pair(self, **kwargs):
            captured.update(kwargs)
            return {"regular_asset": symbol, "white_bg_asset": symbol, "semantic_preservation": {"passed": True}}
    monkeypatch.setattr("app.services.csv_dag_service.PipelineRunner", FakeRunner)
    result = service.execute_task(task.id)
    assert result.status == "completed", result.error_summary
    assert captured["source_asset"].id == base.id
    assert captured["profile"] == SYMBOL_PROFILE
    assert captured["source_profile"] == {"gender": "male", "age": "kid", "skin_color": "white"}
