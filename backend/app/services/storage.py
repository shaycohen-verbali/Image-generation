from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from PIL import Image

from app.core.config import get_settings
from app.services.http_client import get_http_session
from app.services.utils import sanitize_filename

settings = get_settings()

SUPABASE_URI_PREFIX = "supabase://"

_CACHE_LOCK_COUNT = 64
_cache_locks = tuple(threading.Lock() for _ in range(_CACHE_LOCK_COUNT))


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


def _supabase_headers(*, content_type: str = "application/octet-stream", upsert: bool | None = None) -> dict[str, str]:
    token = str(settings.supabase_service_role_key or "").strip()
    headers = {
        "Authorization": f"Bearer {token}",
        "apikey": token,
        "Content-Type": content_type,
    }
    if upsert is not None:
        headers["x-upsert"] = "true" if upsert else "false"
    return headers


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


def _upload_to_supabase(
    bucket: str,
    object_key: str,
    payload: bytes,
    *,
    content_type: str,
    upsert: bool = True,
) -> str:
    response = get_http_session().post(
        _supabase_upload_url(bucket, object_key),
        headers=_supabase_headers(content_type=content_type, upsert=upsert),
        data=payload,
        timeout=120,
    )
    if response.status_code not in {200, 201}:
        raise RuntimeError(f"Supabase upload failed ({response.status_code}): {response.text[:400]}")
    return f"{SUPABASE_URI_PREFIX}{bucket}/{object_key}"


def _download_from_supabase(uri: str) -> bytes:
    bucket, object_key = _parse_supabase_uri(uri)
    response = get_http_session().get(
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


def persist_run_image(
    run_id: str,
    filename: str,
    image_bytes: bytes,
    *,
    mime_type: str,
    storage_prefix: str | None = None,
    upsert: bool = True,
) -> StoredObject:
    local_path = write_image(run_id, filename, image_bytes)
    if storage_backend() != "supabase":
        return StoredObject(local_path=local_path, persisted_path=local_path.as_posix())

    normalized_prefix = str(storage_prefix or f"runs/{sanitize_filename(run_id)}").strip().strip("/")
    object_key = f"{normalized_prefix}/{sanitize_filename(filename)}"
    persisted_path = _upload_to_supabase(
        settings.supabase_image_bucket,
        object_key,
        image_bytes,
        content_type=mime_type,
        upsert=upsert,
    )
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
    try:
        persisted_path = _upload_to_supabase(settings.supabase_export_bucket, object_key, payload, content_type=content_type)
    except RuntimeError as exc:
        # Supabase Storage enforces a per-object size limit. Keep an oversized
        # package on the Render instance so the user can download it immediately
        # instead of failing an otherwise-complete export with HTTP 500.
        if "Payload too large" in str(exc):
            return StoredObject(local_path=local_path, persisted_path=local_path.as_posix())
        raise
    return StoredObject(
        local_path=local_path,
        persisted_path=persisted_path,
        bucket=settings.supabase_export_bucket,
        object_key=object_key,
    )


def export_artifact_uri(export_id: str, filename: str) -> str:
    normalized_id = sanitize_filename(export_id)
    normalized_name = sanitize_filename(filename)
    if storage_backend() == "supabase":
        return f"{SUPABASE_URI_PREFIX}{settings.supabase_export_bucket}/exports/{normalized_id}/{normalized_name}"
    return (exports_root() / normalized_id / normalized_name).as_posix()


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


def materialize_path(path_or_uri: str, *, cache_namespace: str = "assets", force_refresh: bool = False) -> Path:
    value = str(path_or_uri or "").strip()
    if not value:
        raise RuntimeError("Missing storage path")
    if not is_remote_path(value):
        return Path(value)

    bucket, object_key = _parse_supabase_uri(value)
    target = runtime_cache_root() / cache_namespace / bucket / object_key
    target.parent.mkdir(parents=True, exist_ok=True)
    if force_refresh or not target.exists():
        cache_lock = _cache_locks[hash(target.as_posix()) % _CACHE_LOCK_COUNT]
        with cache_lock:
            if force_refresh or not target.exists():
                payload = _download_from_supabase(value)
                with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as tmp:
                    tmp.write(payload)
                    temp_name = tmp.name
                os.replace(temp_name, target)
    return target


def read_binary(path_or_uri: str) -> bytes:
    value = str(path_or_uri or "").strip()
    if is_remote_path(value):
        return _download_from_supabase(value)
    return Path(value).read_bytes()


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def verify_materialized_path(path: Path, expected_sha256: str) -> Path:
    expected = str(expected_sha256 or "").strip().lower()
    # Some legacy rows contain descriptive placeholders rather than a digest.
    # Preserve their readability; all newly written assets have a 64-char SHA-256.
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        return path
    actual = sha256_bytes(path.read_bytes())
    if actual != expected:
        raise RuntimeError(f"Asset checksum mismatch: expected {expected}, received {actual}")
    return path


def selected_asset_path(asset, *, prefer_canonical: bool = False) -> str:
    if prefer_canonical:
        canonical = str(getattr(asset, "canonical_path", "") or "").strip()
        if canonical:
            return canonical
    return str(getattr(asset, "abs_path", "") or "").strip()


def materialize_verified_asset(
    asset,
    *,
    force_refresh: bool = False,
    prefer_canonical: bool = False,
) -> Path:
    asset_id = sanitize_filename(str(getattr(asset, "id", "") or "asset"))
    expected_sha = str(getattr(asset, "sha256", "") or "").strip().lower()
    selected_path = selected_asset_path(asset, prefer_canonical=prefer_canonical)
    path_kind = "canonical" if prefer_canonical and str(getattr(asset, "canonical_path", "") or "").strip() else "attempt"
    namespace = f"assets-{asset_id}-{path_kind}-{expected_sha[:16] or 'legacy'}"
    path = materialize_path(
        selected_path,
        cache_namespace=namespace,
        force_refresh=force_refresh,
    )
    return verify_materialized_path(path, expected_sha)


def promote_run_image(
    run_id: str,
    canonical_filename: str,
    source_path: str,
    *,
    expected_sha256: str,
    mime_type: str,
    storage_prefix: str | None = None,
) -> StoredObject:
    source = materialize_path(source_path, cache_namespace="winner-promotion")
    verify_materialized_path(source, expected_sha256)
    payload = source.read_bytes()
    stored = persist_run_image(
        run_id,
        canonical_filename,
        payload,
        mime_type=mime_type,
        storage_prefix=storage_prefix,
        upsert=True,
    )
    verify_materialized_path(stored.local_path, expected_sha256)
    return stored


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
