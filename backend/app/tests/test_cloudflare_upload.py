from __future__ import annotations

import json
import logging

from sqlalchemy import select

from app.core.config import get_settings
from app.models import CloudUpload, CloudUploadBatch
from app.services.cloudflare_upload import CloudflareUploadService


def test_cloudflare_uploads_images_in_bounded_worker_pool(db_session, tmp_path, monkeypatch, caplog) -> None:
    # Capture the R2 response log emitted by each successful PutObject call.
    caplog.set_level(logging.INFO)
    settings = get_settings()
    settings.cloudflare_r2_endpoint = "https://r2.example.test"
    settings.cloudflare_r2_access_key_id = "test-access"
    settings.cloudflare_r2_secret_access_key = "test-secret"
    settings.cloudflare_r2_upload_workers = 2

    first_path = tmp_path / "first.jpg"
    second_path = tmp_path / "second.jpg"
    first_path.write_bytes(b"first image")
    second_path.write_bytes(b"second image")
    rows = [
        {
            "id": "row-1",
            "word": "first",
            "part_of_speech": "noun",
            "sense_id": "sense-first",
            "kid_male_white_regular_path": first_path.as_posix(),
        },
        {
            "id": "row-2",
            "word": "second",
            "part_of_speech": "noun",
            "sense_id": "sense-second",
            "kid_male_white_regular_path": second_path.as_posix(),
        },
    ]
    batch = CloudUploadBatch(
        id="r2_test_parallel",
        bucket="matalkimages",
        source_rows_json=json.dumps(rows),
        row_count=2,
        status="queued",
    )
    db_session.add(batch)
    db_session.commit()

    class FakeClient:
        def __init__(self) -> None:
            self.keys: list[str] = []

        def put_object(self, **kwargs) -> dict[str, object]:
            self.keys.append(kwargs["Key"])
            return {
                "ETag": '"test-etag"',
                "ResponseMetadata": {
                    "HTTPStatusCode": 200,
                    "RequestId": "request-123",
                },
            }

    fake_client = FakeClient()
    monkeypatch.setattr(CloudflareUploadService, "_client", staticmethod(lambda: fake_client))
    monkeypatch.setattr(
        CloudflareUploadService,
        "_compress",
        staticmethod(lambda payload, quality: b"compressed image"),
    )

    result = CloudflareUploadService(db_session).upload_rows(
        rows,
        bucket="matalkimages",
        batch_id="r2_test_parallel",
    )

    assert result["status"] == "completed"
    assert result["uploaded"] == 2
    assert result["failed"] == 0
    assert "Cloudflare R2 put_object succeeded" in caplog.text
    assert all(
        record.cloudflare_response["ResponseMetadata"]["RequestId"] == "request-123"
        for record in caplog.records
        if record.name == "app.services.cloudflare_upload"
    )
    assert sorted(fake_client.keys) == [
        "first.jpg",
        "second.jpg",
    ]
    ledgers = db_session.scalars(
        select(CloudUpload).where(CloudUpload.batch_id == "r2_test_parallel")
    ).all()
    assert len(ledgers) == 2
    assert {ledger.status for ledger in ledgers} == {"uploaded"}
