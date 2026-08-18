from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

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


def test_cache_age_eviction_removes_old_files(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(storage, "runtime_cache_root", lambda: tmp_path)
    old_file = tmp_path / "assets" / "old.bin"
    old_file.parent.mkdir(parents=True)
    old_file.write_bytes(b"old")
    old_timestamp = 1.0
    os.utime(old_file, (old_timestamp, old_timestamp))

    result = storage.prune_runtime_cache(max_bytes=1000, max_age_seconds=60)

    assert result["removed_files"] == 1
    assert not old_file.exists()


def test_cache_budget_eviction_reaches_eighty_percent_target(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(storage, "runtime_cache_root", lambda: tmp_path)
    for name, payload in (("old.bin", b"1" * 70), ("new.bin", b"2" * 70)):
        path = tmp_path / name
        path.write_bytes(payload)
    now = time.time()
    os.utime(tmp_path / "old.bin", (now - 10, now - 10))
    os.utime(tmp_path / "new.bin", (now - 1, now - 1))

    result = storage.prune_runtime_cache(max_bytes=100, max_age_seconds=1000)

    assert result["remaining_bytes"] <= 80
    assert not (tmp_path / "old.bin").exists()


def test_cache_materialization_rejects_paths_outside_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(storage, "runtime_cache_root", lambda: tmp_path / "cache")

    with pytest.raises(RuntimeError, match="escapes the cache root"):
        storage.materialize_path("supabase://bucket/../outside.bin")


def test_failed_cache_download_leaves_no_partial_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(storage, "runtime_cache_root", lambda: tmp_path)
    monkeypatch.setattr(
        storage,
        "_download_from_supabase",
        lambda _uri: (_ for _ in ()).throw(RuntimeError("download failed")),
    )

    with pytest.raises(RuntimeError, match="download failed"):
        storage.materialize_path("supabase://bucket/path.bin")

    assert not any(path.is_file() for path in tmp_path.rglob("*"))
