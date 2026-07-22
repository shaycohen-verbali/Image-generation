from __future__ import annotations

from app.services import storage


def test_oversized_export_falls_back_to_immediate_local_download(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "exports_root", lambda: tmp_path)
    monkeypatch.setattr(storage, "storage_backend", lambda: "supabase")
    monkeypatch.setattr(
        storage,
        "_upload_to_supabase",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("Supabase upload failed (400): Payload too large")),
    )

    result = storage.persist_export_artifact("export-1", "package.zip", b"zip-data", content_type="application/zip")

    assert result.local_path.read_bytes() == b"zip-data"
    assert result.persisted_path == result.local_path.as_posix()
