from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from PIL import Image

from app.core.config import get_settings
from app.services.http_client import get_http_session
from app.services.utils import sanitize_filename

settings = get_settings()
logger = logging.getLogger(__name__)

SUPABASE_URI_PREFIX = "supabase://"

_CACHE_LOCK_COUNT = 64
_cache_locks = tuple(threading.Lock() for _ in range(_CACHE_LOCK_COUNT))


@dataclass
class StoredObject:
    # ``None`` is intentional for remote-only production persistence.  A
    # caller must use ``persisted_path`` rather than assuming an ephemeral
    # Render filesystem copy exists.
    local_path: Path | None
    persisted_path: str
    bucket: str = ""
    object_key: str = ""


def storage_backend() -> str:
    configured = str(getattr(settings, "storage_backend", "local") or "local").strip().lower()
    if configured == "supabase":
        if settings.supabase_url and settings.supabase_service_role_key:
            return "supabase"
        if str(getattr(settings, "app_env", "dev") or "").strip().lower() == "production":
            raise RuntimeError("STORAGE_BACKEND=supabase requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in production")
    return "local"


def is_remote_path(path: str) -> bool:
    return str(path or "").startswith(SUPABASE_URI_PREFIX)


def production_remote_storage() -> bool:
    return str(getattr(settings, "app_env", "dev") or "").strip().lower() == "production" and storage_backend() == "supabase"


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
    response = get_http_session().post(
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
) -> StoredObject:
    safe_filename = sanitize_filename(filename)
    if storage_backend() != "supabase":
        local_path = write_image(run_id, safe_filename, image_bytes)
        return StoredObject(local_path=local_path, persisted_path=local_path.as_posix())

    normalized_prefix = str(storage_prefix or f"runs/{sanitize_filename(run_id)}").strip().strip("/")
    object_key = f"{normalized_prefix}/{safe_filename}"
    persisted_path = _upload_to_supabase(settings.supabase_image_bucket, object_key, image_bytes, content_type=mime_type)
    return StoredObject(
        local_path=None,
        persisted_path=persisted_path,
        bucket=settings.supabase_image_bucket,
        object_key=object_key,
    )


def persist_export_artifact(export_id: str, filename: str, payload: bytes, *, content_type: str = "application/octet-stream") -> StoredObject:
    backend = storage_backend()
    local_dir = exports_root() / sanitize_filename(export_id)
    local_dir.mkdir(parents=True, exist_ok=True)
    local_path = local_dir / sanitize_filename(filename)
    if backend != "supabase":
        local_path.write_bytes(payload)
        return StoredObject(local_path=local_path, persisted_path=local_path.as_posix())

    # Remote persistence still needs a short-lived staging file for callers
    # that are assembling ZIPs, but it is removed after upload below.
    local_path.write_bytes(payload)
    object_key = f"exports/{sanitize_filename(export_id)}/{sanitize_filename(filename)}"
    try:
        persisted_path = _upload_to_supabase(settings.supabase_export_bucket, object_key, payload, content_type=content_type)
    except RuntimeError as exc:
        # Supabase Storage enforces a per-object size limit. Keep an oversized
        # package locally only for non-production development. A production
        # instance must never report an ephemeral path as a durable export.
        if "Payload too large" in str(exc):
            if production_remote_storage():
                local_path.unlink(missing_ok=True)
                raise RuntimeError(
                    "Durable export storage rejected this artifact; configure a durable export destination before retrying"
                ) from exc
            return StoredObject(local_path=local_path, persisted_path=local_path.as_posix())
        local_path.unlink(missing_ok=True)
        raise
    local_path.unlink(missing_ok=True)
    return StoredObject(
        local_path=None,
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
    backend = storage_backend()
    local_dir = exports_root() / sanitize_filename(job_id)
    local_dir.mkdir(parents=True, exist_ok=True)
    local_path = local_dir / sanitize_filename(filename)
    if backend != "supabase":
        local_path.write_bytes(payload)
        return StoredObject(local_path=local_path, persisted_path=local_path.as_posix())

    object_key = f"csv-jobs/{sanitize_filename(job_id)}/{sanitize_filename(filename)}"
    persisted_path = _upload_to_supabase(settings.supabase_csv_bucket, object_key, payload, content_type="text/csv")
    return StoredObject(
        local_path=None,
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


def _cache_lock(path: Path) -> threading.Lock:
    return _cache_locks[hash(path.as_posix()) % _CACHE_LOCK_COUNT]


def _cache_files(root: Path) -> list[Path]:
    """Return regular files strictly below ``root``; never follow symlinks."""
    resolved_root = root.resolve()
    files: list[Path] = []
    if not root.exists():
        return files
    for candidate in root.rglob("*"):
        if candidate.is_symlink() or not candidate.is_file():
            continue
        try:
            if candidate.resolve().is_relative_to(resolved_root):
                files.append(candidate)
        except OSError:
            continue
    return files


def runtime_cache_usage() -> dict[str, int]:
    files = _cache_files(runtime_cache_root())
    total_bytes = 0
    valid_files = 0
    for path in files:
        try:
            total_bytes += int(path.stat().st_size)
            valid_files += 1
        except OSError:
            continue
    return {"files": valid_files, "bytes": total_bytes}


def prune_runtime_cache(
    max_bytes: int | None = None,
    max_age_seconds: int | None = None,
) -> dict[str, int]:
    """Evict old/LRU materializations without escaping the cache root."""
    byte_limit = max(1, int(max_bytes if max_bytes is not None else settings.storage_cache_max_bytes))
    age_limit = max(1, int(max_age_seconds if max_age_seconds is not None else settings.storage_cache_max_age_seconds))
    root = runtime_cache_root().resolve()
    now = time.time()
    removed_files = 0
    removed_bytes = 0

    def remove_if_unlocked(path: Path) -> bool:
        nonlocal removed_files, removed_bytes
        lock = _cache_lock(path)
        if not lock.acquire(blocking=False):
            return False
        try:
            try:
                resolved = path.resolve()
                if not resolved.is_relative_to(root) or path.is_symlink():
                    return False
                size = int(path.stat().st_size)
                path.unlink(missing_ok=True)
            except OSError:
                return False
            removed_files += 1
            removed_bytes += size
            return True
        finally:
            lock.release()

    files = _cache_files(root)
    for path in files:
        try:
            if now - path.stat().st_mtime > age_limit:
                remove_if_unlocked(path)
        except OSError:
            continue

    remaining = _cache_files(root)
    sizes: list[tuple[float, Path, int]] = []
    current_bytes = 0
    for path in remaining:
        try:
            stat = path.stat()
            size = int(stat.st_size)
            current_bytes += size
            sizes.append((float(getattr(stat, "st_atime", stat.st_mtime)), path, size))
        except OSError:
            continue

    target_bytes = int(byte_limit * 0.8)
    if current_bytes > byte_limit:
        for _atime, path, size in sorted(sizes, key=lambda item: item[0]):
            if current_bytes <= target_bytes:
                break
            if remove_if_unlocked(path):
                current_bytes -= size

    usage = runtime_cache_usage()
    if removed_files:
        logger.info(
            "runtime cache pruned",
            extra={
                "removed_cache_files": removed_files,
                "removed_cache_bytes": removed_bytes,
                "cache_files": usage["files"],
                "cache_bytes": usage["bytes"],
            },
        )
    return {
        "removed_files": removed_files,
        "removed_bytes": removed_bytes,
        "remaining_files": usage["files"],
        "remaining_bytes": usage["bytes"],
    }


def materialize_path(path_or_uri: str, *, cache_namespace: str = "assets", force_refresh: bool = False) -> Path:
    value = str(path_or_uri or "").strip()
    if not value:
        raise RuntimeError("Missing storage path")
    if not is_remote_path(value):
        return Path(value)

    bucket, object_key = _parse_supabase_uri(value)
    if any(part == ".." for part in Path(object_key).parts):
        raise RuntimeError("Storage cache path escapes the cache root")
    cache_root = runtime_cache_root().resolve()
    target = cache_root / sanitize_filename(cache_namespace) / sanitize_filename(bucket) / object_key
    try:
        if not target.parent.resolve().is_relative_to(cache_root) or target.is_symlink():
            raise RuntimeError("Storage cache path escapes the cache root")
    except OSError as exc:
        raise RuntimeError("Storage cache path is unavailable") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    if force_refresh or not target.exists():
        usage = runtime_cache_usage()
        if usage["bytes"] >= int(settings.storage_cache_max_bytes * 0.9):
            prune_runtime_cache()
        cache_lock = _cache_lock(target)
        with cache_lock:
            if force_refresh or not target.exists():
                temp_name: str | None = None
                payload = _download_from_supabase(value)
                try:
                    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as tmp:
                        tmp.write(payload)
                        tmp.flush()
                        temp_name = tmp.name
                    os.replace(temp_name, target)
                finally:
                    if temp_name:
                        try:
                            Path(temp_name).unlink(missing_ok=True)
                        except OSError:
                            pass
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


def image_dimensions_bytes(image_bytes: bytes) -> tuple[int, int]:
    with Image.open(BytesIO(image_bytes)) as img:
        return img.width, img.height


def cleanup_export_staging(export_id: str) -> None:
    """Remove ephemeral export assembly files after remote persistence."""
    try:
        backend = storage_backend()
    except RuntimeError:
        return
    if backend != "supabase":
        return
    root = exports_root().resolve()
    target = (root / sanitize_filename(export_id)).resolve()
    if not target.is_relative_to(root) or target == root:
        return
    import shutil

    shutil.rmtree(target, ignore_errors=True)
