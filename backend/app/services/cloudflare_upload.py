from __future__ import annotations

import hashlib
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from threading import local
from time import monotonic
from typing import Any
from urllib.parse import quote

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import CloudUpload, CloudUploadBatch
from app.services.image_filenames import (
    final_image_filename_for_field,
    inventory_variant,
    versioned_upload_filename,
)
from app.services.storage import materialize_path


logger = logging.getLogger(__name__)


def r2_destination_url(endpoint: str, bucket: str, object_key: str) -> str:
    """Return the exact path-style URL used for the R2 PutObject request."""
    base_url = str(endpoint or "").rstrip("/")
    bucket_path = quote(str(bucket or ""), safe="")
    key_path = quote(str(object_key or ""), safe="/")
    return f"{base_url}/{bucket_path}/{key_path}"


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
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - dependency is installed in deployment
            raise RuntimeError("Cloudflare upload support is missing boto3") from exc
        return boto3.client(
            "s3",
            endpoint_url=settings.cloudflare_r2_endpoint.rstrip("/"),
            region_name="auto",
            aws_access_key_id=settings.cloudflare_r2_access_key_id,
            aws_secret_access_key=settings.cloudflare_r2_secret_access_key,
            config=Config(
                connect_timeout=10,
                read_timeout=30,
                max_pool_connections=max(2, min(32, int(settings.cloudflare_r2_upload_workers or 8))),
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )

    @staticmethod
    def _filename(path: str) -> str:
        return versioned_upload_filename(path)

    @staticmethod
    def _variant(field_name: str) -> str:
        return inventory_variant(field_name)

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
        batch_id = batch_id or f"r2_{uuid.uuid4().hex[:24]}"
        batch = self.db.get(CloudUploadBatch, batch_id)
        if batch is not None:
            batch.status = "running"
            self.db.commit()
        summary = {"batch_id": batch_id, "bucket": selected_bucket, "status": "running", "total": 0, "uploaded": 0, "skipped": 0, "failed": 0, "report_url": f"/api/v1/word-sources/cloud-uploads/{batch_id}/report.csv"}
        completed_count = 0
        last_progress_commit = monotonic()

        def save_progress(*, force: bool = False) -> None:
            nonlocal last_progress_commit
            if batch is None:
                return
            if not force and completed_count % 8 and monotonic() - last_progress_commit < 1.0:
                return
            batch.total = summary["total"]
            batch.uploaded = summary["uploaded"]
            batch.skipped = summary["skipped"]
            batch.failed = summary["failed"]
            self.db.commit()
            last_progress_commit = monotonic()

        # Publish the complete workload before network work starts, then
        # persist each result so polling shows genuine forward progress.
        upload_items: list[dict[str, str]] = []
        for row in rows:
            row_id = str(row.get("_word_source_row_id") or row.get("id") or "")
            sense_id = str(row.get("_word_source_sense_id") or row.get("sense_id") or row_id or "unknown")
            word = str(row.get("_word_source_word") or row.get("word") or "")
            pos = str(row.get("_word_source_part_of_speech") or row.get("part_of_speech") or row.get("part_of_sentence") or "")
            for field_name, source_path in row.items():
                if not str(field_name).endswith("_path") or not str(source_path or "").strip():
                    continue
                variant = self._variant(str(field_name))
                canonical_filename = final_image_filename_for_field(word, pos, str(field_name), sense_id)
                filename = versioned_upload_filename(str(source_path), canonical_filename=canonical_filename)
                # Keep the R2 bucket flat. The object key is the filename only,
                # so uploads land directly under the selected bucket root.
                object_key = filename
                destination_url = r2_destination_url(settings.cloudflare_r2_endpoint, selected_bucket, object_key)
                upload_items.append(
                    {
                        "row_id": row_id,
                        "source_table": str(row.get("_word_source_table") or "word_inventory"),
                        "word": word,
                        "pos": pos,
                        "sense_id": sense_id,
                        "variant": variant,
                        "source_path": str(source_path),
                        "filename": filename,
                        "object_key": object_key,
                        "destination_url": destination_url,
                    }
                )

        summary["total"] = len(upload_items)
        save_progress(force=True)

        # Do not probe R2 with HeadObject. Cloudflare credentials can permit
        # PutObject while denying HEAD, and that probe previously stalled entire
        # batches before image one. Load the durable ledger once instead of
        # issuing one database query and commit per image.
        object_keys = list(dict.fromkeys(item["object_key"] for item in upload_items))
        existing_by_key: dict[str, CloudUpload] = {}
        for offset in range(0, len(object_keys), 500):
            existing_rows = self.db.scalars(
                select(CloudUpload).where(
                    CloudUpload.bucket == selected_bucket,
                    CloudUpload.object_key.in_(object_keys[offset:offset + 500]),
                )
            ).all()
            existing_by_key.update({row.object_key: row for row in existing_rows})

        pending_uploads: list[tuple[dict[str, str], CloudUpload]] = []
        scheduled_object_keys: set[str] = set()
        for item in upload_items:
            object_key = item["object_key"]
            existing = existing_by_key.get(object_key)
            if existing is not None:
                existing.destination_url = item["destination_url"]
            if existing and existing.status == "uploaded":
                summary["skipped"] += 1
                continue
            if object_key in scheduled_object_keys:
                summary["skipped"] += 1
                continue
            # One object key is uploaded once even if the same source path was
            # selected through more than one duplicate inventory row.
            ledger = existing or CloudUpload(
                batch_id=batch_id,
                source_table=item["source_table"],
                source_row_id=item["row_id"],
                word=item["word"],
                part_of_speech=item["pos"],
                sense_id=item["sense_id"],
                variant=item["variant"],
                source_path=item["source_path"],
                original_filename=item["filename"],
                bucket=selected_bucket,
                object_key=object_key,
                destination_url=item["destination_url"],
                compression_quality=compression_quality,
            )
            ledger.batch_id = batch_id
            ledger.destination_url = item["destination_url"]
            ledger.status = "uploading"
            ledger.error_detail = ""
            self.db.add(ledger)
            pending_uploads.append((item, ledger))
            scheduled_object_keys.add(object_key)

        save_progress(force=True)

        # Compression and object uploads are independent per image. Keep the
        # concurrency bounded so one export is much faster without overwhelming
        # Render, Supabase downloads, or the R2 connection pool.
        self._client()  # Validate credentials before starting worker threads.
        worker_state = local()

        def upload_one(item: dict[str, str]) -> dict[str, Any]:
            try:
                client = getattr(worker_state, "client", None)
                if client is None:
                    client = self._client()
                    worker_state.client = client
                original = materialize_path(item["source_path"], cache_namespace="cloudflare_uploads").read_bytes()
                compressed = self._compress(original, compression_quality)
                cloudflare_response = client.put_object(
                    Bucket=selected_bucket,
                    Key=item["object_key"],
                    Body=compressed,
                    ContentType="image/jpeg",
                    Metadata={"word": item["word"][:512], "part-of-speech": item["pos"][:256], "variant": item["variant"][:512]},
                )
                logger.info(
                    "Cloudflare R2 put_object succeeded",
                    extra={
                        "cloudflare_batch_id": batch_id,
                        "cloudflare_bucket": selected_bucket,
                        "cloudflare_key": item["object_key"],
                        "cloudflare_response": cloudflare_response,
                    },
                )
                return {
                    "status": "uploaded",
                    "original_bytes": len(original),
                    "compressed_bytes": len(compressed),
                    "source_sha256": hashlib.sha256(original).hexdigest(),
                    "compressed_sha256": hashlib.sha256(compressed).hexdigest(),
                }
            except Exception as exc:  # keep the batch moving when one source path is bad
                error_code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
                logger.exception(
                    "Cloudflare R2 image upload failed",
                    extra={
                        "cloudflare_batch_id": batch_id,
                        "cloudflare_bucket": selected_bucket,
                        "cloudflare_key": item["object_key"],
                        "cloudflare_error": str(exc)[:2000],
                    },
                )
                return {
                    "status": "failed",
                    "error_detail": str(exc)[:2000],
                    "authentication_error": error_code in {
                        "AccessDenied",
                        "InvalidAccessKeyId",
                        "InvalidToken",
                        "SignatureDoesNotMatch",
                    },
                }

        authentication_error_detail = ""
        upload_worker_count = max(1, min(32, int(settings.cloudflare_r2_upload_workers or 8)))
        if pending_uploads:
            with ThreadPoolExecutor(max_workers=min(upload_worker_count, len(pending_uploads))) as executor:
                pending_futures = {
                    executor.submit(upload_one, item): (item, ledger)
                    for item, ledger in pending_uploads
                }
                for future in as_completed(pending_futures):
                    item, ledger = pending_futures[future]
                    result = future.result()
                    completed_count += 1
                    if result["status"] == "uploaded":
                        ledger.status = "uploaded"
                        ledger.original_bytes = result["original_bytes"]
                        ledger.compressed_bytes = result["compressed_bytes"]
                        ledger.source_sha256 = result["source_sha256"]
                        ledger.compressed_sha256 = result["compressed_sha256"]
                        summary["uploaded"] += 1
                    else:
                        ledger.status = "failed"
                        ledger.error_detail = result["error_detail"]
                        summary["failed"] += 1
                        if batch is not None and not batch.error_detail:
                            batch.error_detail = result["error_detail"]
                        if result.get("authentication_error") and not authentication_error_detail:
                            authentication_error_detail = result["error_detail"]
                    save_progress()

        save_progress(force=True)
        if authentication_error_detail:
            raise RuntimeError(authentication_error_detail)
        if batch is not None:
            batch.total = summary["total"]
            batch.uploaded = summary["uploaded"]
            batch.skipped = summary["skipped"]
            batch.failed = summary["failed"]
            batch.status = "completed" if not summary["failed"] else "completed_with_errors"
            self.db.commit()
        summary["status"] = batch.status if batch is not None else "completed"
        return summary
