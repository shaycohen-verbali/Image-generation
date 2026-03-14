from __future__ import annotations

import hashlib
import json
import tempfile
from io import BytesIO
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


def run_temp_dir(run_id: str) -> Path:
    path = run_dir(run_id) / "tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_image(run_id: str, filename: str, image_bytes: bytes) -> Path:
    path = run_dir(run_id) / sanitize_filename(filename)
    path.write_bytes(image_bytes)
    return path


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


def image_dimensions(path: Path) -> tuple[int, int]:
    with Image.open(path) as img:
        return img.width, img.height
