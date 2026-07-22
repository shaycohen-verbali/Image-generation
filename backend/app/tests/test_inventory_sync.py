from __future__ import annotations

from app.services.inventory_sync import normalize_csv_job_export_fields
from app.services.inventory_sync import InventorySyncService


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
