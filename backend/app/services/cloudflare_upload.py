from __future__ import annotations

import hashlib
import re
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import CloudUpload, CloudUploadBatch
from app.services.storage import materialize_path


_PATH_FIELD_RE = re.compile(r"^(?P<age>[^_]+)_(?P<gender>[^_]+)_(?P<skin>[^_]+)_(?P<background>regular|white_bg)_path$")


def configured_buckets() -> list[str]:
    raw = str(get_settings().cloudflare_r2_buckets or "").strip()
    return list(dict.fromkeys(value.strip() for value in raw.split(",") if value.strip()))


class CloudflareUploadService:
    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _client():
        settings = get_settings()
        if not settings.cloudflare_r2_endpoint or not settings.cloudflare_r2_access_key_id or not settings.cloudflare_r2_secret_access_key:
            raise RuntimeError("Cloudflare R2 is not configured. Add the R2 endpoint, access key ID, and secret access key to Render.")
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - dependency is installed in deployment
            raise RuntimeError("Cloudflare upload support is missing boto3") from exc
        return boto3.client(
            "s3",
            endpoint_url=settings.cloudflare_r2_endpoint.rstrip("/"),
            region_name="auto",
            aws_access_key_id=settings.cloudflare_r2_access_key_id,
            aws_secret_access_key=settings.cloudflare_r2_secret_access_key,
        )

    @staticmethod
    def _filename(path: str) -> str:
        value = str(path or "").split("/")[-1] or "image.jpg"
        # Inventory image names are already safe and meaningful. Restrict only
        # path separators so the original basename remains intact in R2.
        value = value.replace("\\", "_").replace("/", "_")
        if Path(value).suffix.lower() not in {".jpg", ".jpeg"}:
            value = f"{Path(value).stem}.jpg"
        return value

    @staticmethod
    def _variant(field_name: str) -> str:
        match = _PATH_FIELD_RE.match(field_name)
        if not match:
            return field_name.removesuffix("_path")
        background = "white_background" if match.group("background") == "white_bg" else "regular"
        return f"{match.group('age')}/{match.group('gender')}/{match.group('skin')}/{background}"

    @staticmethod
    def _compress(payload: bytes, quality: int) -> bytes:
        with Image.open(BytesIO(payload)) as image:
            if image.mode in {"RGBA", "LA", "P"}:
                background = Image.new("RGB", image.size, "white")
                if image.mode == "P":
                    image = image.convert("RGBA")
                background.paste(image, mask=image.getchannel("A"))
                image = background
            else:
                image = image.convert("RGB")
            output = BytesIO()
            image.save(output, format="JPEG", quality=max(1, min(100, int(quality))), optimize=True, progressive=True)
            return output.getvalue()

    def upload_rows(self, rows: list[dict[str, Any]], *, bucket: str, quality: int | None = None, batch_id: str | None = None) -> dict[str, Any]:
        settings = get_settings()
        allowed = configured_buckets()
        selected_bucket = str(bucket or settings.cloudflare_r2_default_bucket or "").strip()
        if not selected_bucket:
            raise ValueError("Choose a Cloudflare R2 bucket")
        if allowed and selected_bucket not in allowed:
            raise ValueError("That Cloudflare R2 bucket is not configured")
        compression_quality = max(1, min(100, int(quality or settings.cloudflare_r2_compression_quality)))
        client = self._client()
        batch_id = batch_id or f"r2_{uuid.uuid4().hex[:24]}"
        prefix = str(settings.cloudflare_r2_key_prefix or "word_inventory").strip().strip("/")
        batch = self.db.get(CloudUploadBatch, batch_id)
        if batch is not None:
            batch.status = "running"
            self.db.commit()
        summary = {"batch_id": batch_id, "bucket": selected_bucket, "status": "running", "total": 0, "uploaded": 0, "skipped": 0, "failed": 0, "report_url": f"/api/v1/word-sources/cloud-uploads/{batch_id}/report.csv"}

        for row in rows:
            row_id = str(row.get("_word_source_row_id") or row.get("id") or "")
            sense_id = str(row.get("_word_source_sense_id") or row.get("sense_id") or row_id or "unknown")
            word = str(row.get("_word_source_word") or row.get("word") or "")
            pos = str(row.get("_word_source_part_of_speech") or row.get("part_of_speech") or row.get("part_of_sentence") or "")
            for field_name, source_path in row.items():
                if not str(field_name).endswith("_path") or not str(source_path or "").strip():
                    continue
                summary["total"] += 1
                variant = self._variant(str(field_name))
                filename = self._filename(str(source_path))
                object_key = f"{prefix}/{sense_id}/{variant}/{filename}"
                existing = self.db.scalar(select(CloudUpload).where(CloudUpload.bucket == selected_bucket, CloudUpload.object_key == object_key))
                if existing and existing.status == "uploaded":
                    summary["skipped"] += 1
                    continue
                if existing is None:
                    try:
                        client.head_object(Bucket=selected_bucket, Key=object_key)
                    except Exception as exc:
                        code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
                        # Some R2 tokens allow writes but deny HEAD/metadata
                        # reads. The existence check is only an optimization;
                        # still attempt the upload and let PutObject enforce
                        # the token's actual write permission.
                        if code not in {"403", "Forbidden", "404", "NoSuchKey", "NotFound"}:
                            raise
                    else:
                        existing = CloudUpload(
                            batch_id=batch_id,
                            source_table=str(row.get("_word_source_table") or "word_inventory"),
                            source_row_id=row_id,
                            word=word,
                            part_of_speech=pos,
                            sense_id=sense_id,
                            variant=variant,
                            source_path=str(source_path),
                            original_filename=filename,
                            bucket=selected_bucket,
                            object_key=object_key,
                            status="skipped",
                            compression_quality=compression_quality,
                        )
                        self.db.add(existing)
                        self.db.commit()
                        summary["skipped"] += 1
                        continue
                ledger = existing or CloudUpload(
                    batch_id=batch_id,
                    source_table=str(row.get("_word_source_table") or "word_inventory"),
                    source_row_id=row_id,
                    word=word,
                    part_of_speech=pos,
                    sense_id=sense_id,
                    variant=variant,
                    source_path=str(source_path),
                    original_filename=filename,
                    bucket=selected_bucket,
                    object_key=object_key,
                    compression_quality=compression_quality,
                )
                ledger.batch_id = batch_id
                ledger.status = "uploading"
                ledger.error_detail = ""
                self.db.add(ledger)
                self.db.commit()
                try:
                    original = materialize_path(str(source_path), cache_namespace="cloudflare_uploads").read_bytes()
                    compressed = self._compress(original, compression_quality)
                    client.put_object(
                        Bucket=selected_bucket,
                        Key=object_key,
                        Body=compressed,
                        ContentType="image/jpeg",
                        Metadata={"word": word[:512], "part-of-speech": pos[:256], "variant": variant[:512]},
                    )
                    ledger.status = "uploaded"
                    ledger.original_bytes = len(original)
                    ledger.compressed_bytes = len(compressed)
                    ledger.source_sha256 = hashlib.sha256(original).hexdigest()
                    ledger.compressed_sha256 = hashlib.sha256(compressed).hexdigest()
                    summary["uploaded"] += 1
                except Exception as exc:  # keep the batch moving when one source path is bad
                    ledger.status = "failed"
                    ledger.error_detail = str(exc)[:2000]
                    summary["failed"] += 1
                self.db.commit()
        if batch is not None:
            batch.total = summary["total"]
            batch.uploaded = summary["uploaded"]
            batch.skipped = summary["skipped"]
            batch.failed = summary["failed"]
            batch.status = "completed" if not summary["failed"] else "completed_with_errors"
            self.db.commit()
        summary["status"] = batch.status if batch is not None else "completed"
        return summary
