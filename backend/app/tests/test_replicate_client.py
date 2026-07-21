from app.core.config import get_settings
from app.services.replicate_client import ReplicateClient


def test_replicate_client_uses_direct_api_by_default(monkeypatch) -> None:
    monkeypatch.delenv("REPLICATE_BASE_URL", raising=False)
    monkeypatch.setenv("REPLICATE_API_TOKEN", "r8_test")
    get_settings.cache_clear()

    try:
        client = ReplicateClient()
        assert client.base_url == "https://api.replicate.com"
    finally:
        get_settings.cache_clear()


def test_replicate_client_accepts_base_url_override(monkeypatch) -> None:
    monkeypatch.setenv("REPLICATE_BASE_URL", "https://replicate.example.test/")
    monkeypatch.setenv("REPLICATE_API_TOKEN", "r8_test")
    get_settings.cache_clear()

    try:
        client = ReplicateClient()
        assert client.base_url == "https://replicate.example.test"
    finally:
        get_settings.cache_clear()
