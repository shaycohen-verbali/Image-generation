from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image

from app.core.config import get_settings
from app.services.utils import sanitize_filename

settings = get_settings()

SUPABASE_URI_PREFIX = "supabase://"


@dataclass
class StoredObject:
    local_path: Path
    persisted_path: str
    bucket: str = ""
    object_key: str = ""


def storage_backend() -> str:
    configured = str(getattr(settings, "storage_backend", "local") or "local").strip().lower()
    if configured == "supabase" and settings.supabase_url and settings.supabase_service_role_key:
        return "supabase"
    return "local"


def is_remote_path(path: str) -> bool:
    return str(path or "").startswith(SUPABASE_URI_PREFIX)


def _supabase_headers(*, content_type: str = "application/octet-stream") -> dict[str, str]:
    token = str(settings.supabase_service_role_key or "").strip()
    return {
        "Authorization": f"Bearer {token}",
        "apikey": token,
        "Content-Type": content_type,
        "x-upsert": "true",
    }


def _supabase_upload_url(bucket: str, object_key: str) -> str:
    base = str(settings.supabase_url or "").rstrip("/")
    return f"{base}/storage/v1/object/{quote(bucket)}/{quote(object_key)}"


def _supabase_download_url(bucket: str, object_key: str) -> str:
    base = str(settings.supabase_url or "").rstrip("/")
    return f"{base}/storage/v1/object/{quote(bucket)}/{quote(object_key)}"


def _parse_supabase_uri(uri: str) -> tuple[str, str]:
    raw = str(uri or "").removeprefix(SUPABASE_URI_PREFIX)
    bucket, _, key = raw.partition("/")
    if not bucket or not key:
        raise RuntimeError(f"Invalid Supabase storage URI: {uri}")
    return bucket, key


def _upload_to_supabase(bucket: str, object_key: str, payload: bytes, *, content_type: str) -> str:
    response = requests.post(
        _supabase_upload_url(bucket, object_key),
        headers=_supabase_headers(content_type=content_type),
        data=payload,
        timeout=120,
    )
    if response.status_code not in {200, 201}:
        raise RuntimeError(f"Supabase upload failed ({response.status_code}): {response.text[:400]}")
    return f"{SUPABASE_URI_PREFIX}{bucket}/{object_key}"


def _download_from_supabase(uri: str) -> bytes:
    bucket, object_key = _parse_supabase_uri(uri)
    response = requests.get(
        _supabase_download_url(bucket, object_key),
        headers=_supabase_headers(),
        timeout=120,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Supabase download failed ({response.status_code}): {response.text[:400]}")
    return response.content


def runtime_cache_root() -> Path:
    root = settings.runtime_data_root / "cache"
    root.mkdir(parents=True, exist_ok=True)
    return root


def runs_root() -> Path:
    root = settings.runtime_data_root / "runs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def exports_root() -> Path:
    root = settings.runtime_data_root / "exports"
    root.mkdir(parents=True, exist_ok=True)
    return root


def run_dir(run_id: str) -> Path:
    path = runs_root() / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_temp_dir(run_id: str) -> Path:
    path = run_dir(run_id) / "tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_image(run_id: str, filename: str, image_bytes: bytes) -> Path:
    path = run_dir(run_id) / sanitize_filename(filename)
    path.write_bytes(image_bytes)
    return path


def persist_run_image(run_id: str, filename: str, image_bytes: bytes, *, mime_type: str) -> StoredObject:
    local_path = write_image(run_id, filename, image_bytes)
    if storage_backend() != "supabase":
        return StoredObject(local_path=local_path, persisted_path=local_path.as_posix())

    object_key = f"runs/{sanitize_filename(run_id)}/{sanitize_filename(filename)}"
    persisted_path = _upload_to_supabase(settings.supabase_image_bucket, object_key, image_bytes, content_type=mime_type)
    return StoredObject(
        local_path=local_path,
        persisted_path=persisted_path,
        bucket=settings.supabase_image_bucket,
        object_key=object_key,
    )


def persist_export_artifact(export_id: str, filename: str, payload: bytes, *, content_type: str = "application/octet-stream") -> StoredObject:
    local_dir = exports_root() / sanitize_filename(export_id)
    local_dir.mkdir(parents=True, exist_ok=True)
    local_path = local_dir / sanitize_filename(filename)
    local_path.write_bytes(payload)
    if storage_backend() != "supabase":
        return StoredObject(local_path=local_path, persisted_path=local_path.as_posix())

    object_key = f"exports/{sanitize_filename(export_id)}/{sanitize_filename(filename)}"
    persisted_path = _upload_to_supabase(settings.supabase_export_bucket, object_key, payload, content_type=content_type)
    return StoredObject(
        local_path=local_path,
        persisted_path=persisted_path,
        bucket=settings.supabase_export_bucket,
        object_key=object_key,
    )


def persist_csv_source(job_id: str, filename: str, payload: bytes) -> StoredObject:
    local_dir = exports_root() / sanitize_filename(job_id)
    local_dir.mkdir(parents=True, exist_ok=True)
    local_path = local_dir / sanitize_filename(filename)
    local_path.write_bytes(payload)
    if storage_backend() != "supabase":
        return StoredObject(local_path=local_path, persisted_path=local_path.as_posix())

    object_key = f"csv-jobs/{sanitize_filename(job_id)}/{sanitize_filename(filename)}"
    persisted_path = _upload_to_supabase(settings.supabase_csv_bucket, object_key, payload, content_type="text/csv")
    return StoredObject(
        local_path=local_path,
        persisted_path=persisted_path,
        bucket=settings.supabase_csv_bucket,
        object_key=object_key,
    )


def write_temp_binary(run_id: str, *, suffix: str, payload: bytes, prefix: str = "google_inline_") -> Path:
    with tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=prefix,
        suffix=suffix,
        dir=run_temp_dir(run_id),
        delete=False,
    ) as tmp:
        tmp.write(payload)
        return Path(tmp.name)


def write_metadata(run_id: str, attempt: int, payload: dict) -> Path:
    path = run_dir(run_id) / f"metadata_attempt_{attempt}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def materialize_path(path_or_uri: str, *, cache_namespace: str = "assets") -> Path:
    value = str(path_or_uri or "").strip()
    if not value:
        raise RuntimeError("Missing storage path")
    if not is_remote_path(value):
        return Path(value)

    bucket, object_key = _parse_supabase_uri(value)
    target = runtime_cache_root() / cache_namespace / bucket / object_key
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(_download_from_supabase(value))
    return target


def read_binary(path_or_uri: str) -> bytes:
    value = str(path_or_uri or "").strip()
    if is_remote_path(value):
        return _download_from_supabase(value)
    return Path(value).read_bytes()


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def normalize_saved_image(image_bytes: bytes, output_mime_type: str) -> tuple[bytes, str, str]:
    output_mime = str(output_mime_type or "image/jpeg").strip().lower()
    format_name = "JPEG"
    suffix = ".jpg"
    save_kwargs: dict[str, object] = {}
    if output_mime == "image/png":
        format_name = "PNG"
        suffix = ".png"
    elif output_mime == "image/webp":
        format_name = "WEBP"
        suffix = ".webp"
    else:
        output_mime = "image/jpeg"
        save_kwargs["quality"] = 95

    with Image.open(BytesIO(image_bytes)) as img:
        image = img.copy()
    if output_mime == "image/jpeg":
        if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
            rgba_image = image.convert("RGBA")
            background = Image.new("RGB", image.size, (255, 255, 255))
            background.paste(rgba_image, mask=rgba_image.getchannel("A"))
            image = background
        elif image.mode != "RGB":
            image = image.convert("RGB")

    buffer = BytesIO()
    image.save(buffer, format=format_name, **save_kwargs)
    return buffer.getvalue(), output_mime, suffix


def image_dimensions(path_or_uri: Path | str) -> tuple[int, int]:
    materialized = materialize_path(path_or_uri.as_posix() if isinstance(path_or_uri, Path) else path_or_uri)
    with Image.open(materialized) as img:
        return img.width, img.height
