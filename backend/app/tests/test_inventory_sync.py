from __future__ import annotations

from app.services.inventory_sync import normalize_csv_job_export_fields
from app.services.inventory_sync import InventorySyncService
from app.services.library import _winning_prompts_by_path
from app.services.repository import Repository


def test_normalize_csv_job_export_fields_filters_invalid_and_duplicate_keys() -> None:
    normalized = normalize_csv_job_export_fields(
        ["word", "word", "teenager_female_white_regular_path", "not_real_field"]
    )

    assert normalized == ["word", "teenager_female_white_regular_path"]


def test_sense_image_sync_retries_and_sends_server_side_payload(db_session, monkeypatch) -> None:
    settings = __import__("app.core.config", fromlist=["get_settings"]).get_settings()
    settings.supabase_sense_images_sync_rpc_url = "https://example.supabase.co/rest/v1/rpc/aac_sync_sense_images_from_inventory"
    settings.supabase_service_role_key = "server-only-test-key"
    calls = []

    class Response:
        def __init__(self, status_code: int, text: str = ""):
            self.status_code = status_code
            self.text = text

    class Session:
        def post(self, url, *, headers, json, timeout):
            calls.append((url, headers, json, timeout))
            return Response(500, "temporary") if len(calls) == 1 else Response(204)

    monkeypatch.setattr("app.services.inventory_sync.get_http_session", lambda: Session())
    monkeypatch.setattr("app.services.inventory_sync.time.sleep", lambda _seconds: None)

    assert InventorySyncService(db_session).sync_sense_images(reason="test", retries=2) is True
    assert len(calls) == 2
    url, headers, payload, timeout = calls[-1]
    assert url.endswith("/aac_sync_sense_images_from_inventory")
    assert headers["Authorization"] == "Bearer server-only-test-key"
    assert payload == {
        "requested_image_style": "aac_current",
        "requested_style_version": "1",
        "requested_storage_bucket": "aac-images-v1",
    }
    assert timeout == 20


def test_softened_asset_uses_winning_source_prompt_for_inventory(db_session) -> None:
    repo = Repository(db_session)
    entry = repo.create_entry({
        "word": "abbey",
        "part_of_sentence": "noun",
        "category": "a church associated with a monastery or convent",
        "context": "AAC",
        "person_gender_options": ["male"],
        "person_age_options": ["kid"],
        "person_skin_color_options": ["white"],
        "batch": "test",
    })
    run = repo.create_shadow_run(entry_id=entry.id, quality_threshold=95, max_optimization_attempts=3)
    winning_prompt = repo.add_prompt(
        run_id=run.id,
        stage_name="stage3_upgrade",
        attempt=1,
        prompt_text="Create the winning abbey image.",
        needs_person="yes",
        source="test",
        raw_response_json={},
    )
    soften_prompt = repo.add_prompt(
        run_id=run.id,
        stage_name="stage3_post_quality_accessibility_generate",
        attempt=1,
        prompt_text="Soften the background.",
        needs_person="yes",
        source="test",
        raw_response_json={},
    )
    winning_asset = repo.add_asset(
        run_id=run.id,
        stage_name="stage3_upgraded",
        attempt=1,
        file_name="abbey-winning.jpg",
        abs_path="/tmp/abbey-winning.jpg",
        mime_type="image/jpeg",
        sha256="winning",
        width=100,
        height=100,
        origin_url="",
        model_name="test",
        generation_prompt_id=winning_prompt.id,
    )
    softened_asset = repo.add_asset(
        run_id=run.id,
        stage_name="stage3_post_quality_accessibility_generate",
        attempt=1,
        file_name="abbey-softened.jpg",
        abs_path="/tmp/abbey-softened.jpg",
        mime_type="image/jpeg",
        sha256="softened",
        width=100,
        height=100,
        origin_url="",
        model_name="test",
        generation_prompt_id=soften_prompt.id,
        source_asset_id=winning_asset.id,
    )

    selected = InventorySyncService(db_session)._prompt_for_asset(
        softened_asset,
        legacy_stage_name="stage3_upgrade",
    )

    assert selected is not None
    assert selected.id == winning_prompt.id
    assert selected.prompt_text == "Create the winning abbey image."

    resolved = _winning_prompts_by_path(db_session.connection(), {softened_asset.abs_path})
    assert resolved[softened_asset.abs_path] == "Create the winning abbey image."
