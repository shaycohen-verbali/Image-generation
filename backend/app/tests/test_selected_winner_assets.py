from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

from app.core.config import get_settings
from app.services.pipeline import PipelineRunner
from app.services.repository import Repository


def _jpeg_bytes(color: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (12, 12), color).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_attempts_are_retained_when_a_new_winner_replaces_the_canonical_file(
    db_session, monkeypatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "selected_winner_flow_enabled", True)
    runner = PipelineRunner(db_session)
    canonical_name = "apple__noun__reg__m__kid__w__sense.jpg"

    first = runner._save_asset(
        run_id="run_override",
        stage_name="stage3_upgraded",
        attempt=1,
        filename=canonical_name,
        image_bytes=_jpeg_bytes((255, 0, 0)),
        origin_url="",
        model_name="test",
    )
    second = runner._save_asset(
        run_id="run_override",
        stage_name="stage3_upgraded",
        attempt=2,
        filename=canonical_name,
        image_bytes=_jpeg_bytes((0, 0, 255)),
        origin_url="",
        model_name="test",
    )

    assert first.abs_path != second.abs_path
    assert "__attempt_01__" in first.file_name
    assert "__attempt_02__" in second.file_name
    assert Path(first.abs_path).parent == Path(second.abs_path).parent
    assert Path(first.abs_path).exists()
    assert Path(second.abs_path).exists()

    first = runner._promote_asset(first, canonical_filename=canonical_name)
    canonical_path = Path(first.canonical_path or "")
    first_winner_bytes = canonical_path.read_bytes()
    second = runner._promote_asset(second, canonical_filename=canonical_name)

    refreshed_first = Repository(db_session).get_asset(first.id)
    refreshed_second = Repository(db_session).get_asset(second.id)
    assert refreshed_first is not None and refreshed_first.canonical_path is None
    assert refreshed_second is not None and refreshed_second.canonical_path == canonical_path.as_posix()
    assert canonical_path.read_bytes() != first_winner_bytes
    assert canonical_path.read_bytes() == Path(second.abs_path).read_bytes()
    assert Path(first.abs_path).exists()
    assert Path(second.abs_path).exists()


def test_canonical_promotion_rejects_tampered_attempt_bytes(db_session, monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "selected_winner_flow_enabled", True)
    runner = PipelineRunner(db_session)
    asset = runner._save_asset(
        run_id="run_checksum",
        stage_name="stage3_upgraded",
        attempt=1,
        filename="apple.jpg",
        image_bytes=_jpeg_bytes((255, 0, 0)),
        origin_url="",
        model_name="test",
    )
    Path(asset.abs_path).write_bytes(b"tampered")

    try:
        runner._promote_asset(asset, canonical_filename="apple.jpg")
    except RuntimeError as exc:
        assert "checksum mismatch" in str(exc).lower()
    else:
        raise AssertionError("tampered attempt was promoted")
