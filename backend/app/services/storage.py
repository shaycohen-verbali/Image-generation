from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image

from app.core.config import get_settings
from app.services.utils import sanitize_filename

settings = get_settings()


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


def write_image(run_id: str, filename: str, image_bytes: bytes) -> Path:
    path = run_dir(run_id) / sanitize_filename(filename)
    path.write_bytes(image_bytes)
    return path


def write_metadata(run_id: str, attempt: int, payload: dict) -> Path:
    path = run_dir(run_id) / f"metadata_attempt_{attempt}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def image_dimensions(path: Path) -> tuple[int, int]:
    with Image.open(path) as img:
        return img.width, img.height
