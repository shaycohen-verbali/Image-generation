from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

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


def test_oversized_production_export_fails_instead_of_returning_ephemeral_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "exports_root", lambda: tmp_path)
    monkeypatch.setattr(storage, "storage_backend", lambda: "supabase")
    monkeypatch.setattr(storage.settings, "app_env", "production")
    monkeypatch.setattr(
        storage,
        "_upload_to_supabase",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("Supabase upload failed (400): Payload too large")),
    )

    with pytest.raises(RuntimeError, match="Durable export storage rejected"):
        storage.persist_export_artifact("export-prod", "package.zip", b"zip-data", content_type="application/zip")

    assert not (tmp_path / "export-prod" / "package.zip").exists()


def test_remote_run_image_does_not_create_permanent_local_copy(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(storage.settings, "runtime_data_root", tmp_path)
    monkeypatch.setattr(storage, "storage_backend", lambda: "supabase")
    monkeypatch.setattr(storage.settings, "supabase_image_bucket", "generated-images")
    monkeypatch.setattr(
        storage,
        "_upload_to_supabase",
        lambda bucket, key, payload, *, content_type: f"supabase://{bucket}/{key}",
    )

    result = storage.persist_run_image(
        "run-remote",
        "image.jpg",
        b"image-bytes",
        mime_type="image/jpeg",
    )

    assert result.local_path is None
    assert result.persisted_path == "supabase://generated-images/runs/run-remote/image.jpg"
    assert not (tmp_path / "runs" / "run-remote" / "image.jpg").exists()


def test_image_dimensions_can_be_read_without_materializing_a_file() -> None:
    output = BytesIO()
    with Image.new("RGB", (17, 23), "white") as image:
        image.save(output, format="PNG")

    assert storage.image_dimensions_bytes(output.getvalue()) == (17, 23)
