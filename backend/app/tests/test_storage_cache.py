from __future__ import annotations

from pathlib import Path

from app.services import storage


def test_materialize_path_reuses_cached_remote_image(monkeypatch, tmp_path: Path) -> None:
    downloads: list[str] = []

    monkeypatch.setattr(storage, "runtime_cache_root", lambda: tmp_path)

    def _download(uri: str) -> bytes:
        downloads.append(uri)
        return b"image-content"

    monkeypatch.setattr(storage, "_download_from_supabase", _download)
    uri = "supabase://generated-images/runs/run-1/image.jpg"

    first_path = storage.materialize_path(uri)
    second_path = storage.materialize_path(uri)

    assert first_path == second_path
    assert first_path.read_bytes() == b"image-content"
    assert downloads == [uri]


def test_materialize_path_can_explicitly_refresh_cache(monkeypatch, tmp_path: Path) -> None:
    payloads = iter([b"first", b"second"])
    monkeypatch.setattr(storage, "runtime_cache_root", lambda: tmp_path)
    monkeypatch.setattr(storage, "_download_from_supabase", lambda _uri: next(payloads))
    uri = "supabase://generated-images/runs/run-1/image.jpg"

    cached_path = storage.materialize_path(uri)
    refreshed_path = storage.materialize_path(uri, force_refresh=True)

    assert cached_path == refreshed_path
    assert refreshed_path.read_bytes() == b"second"
