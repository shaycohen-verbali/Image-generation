from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import CloudUpload


MATALK_DICTIONARY_FIELDS = (
    "sense_id",
    "source_word",
    "normalized_word",
    "lemmatized_word",
    "part_of_speech",
    "source_main_category",
    "source_fine_tune_categories",
    "canonical_sense_id",
    "canonical_word",
    "lookup_status",
    "sense_oxford",
    "sense_wordnet",
    "synonyms",
)

MATALK_IMAGE_META_FIELDS = (
    "canonical_sense_id",
    "word",
    "part_of_speech",
    "category",
    "has_person",
    "image_score",
    "is_active",
)

MATALK_IMAGES_FIELDS = (
    "canonical_sense_id",
    "age",
    "gender",
    "skin",
    "background",
    "image_url",
)

MATALK_ARTIFACT_FILENAMES = {
    "dictionary": "aac_dictionary.csv",
    "image_meta": "aac_image_meta.csv",
    "images": "aac_images.csv",
    "manifest": "matalk_manifest.json",
    "readme": "matalk_README.md",
}

_PATH_FIELD_RE = re.compile(
    r"^(?P<age>toddler|kid|tween|teenager)_(?P<gender>male|female)_"
    r"(?P<skin>white|black|asian|brown)_(?P<background>regular|white_bg)_path$"
)
_FALSE_VALUES = {"0", "false", "no", "n", "off", "inactive", "disabled"}
_TRUE_VALUES = {"1", "true", "yes", "y", "on", "active", "enabled"}


@dataclass(frozen=True)
class MatalkTables:
    dictionary_rows: tuple[dict[str, Any], ...]
    image_meta_rows: tuple[dict[str, Any], ...]
    image_rows: tuple[dict[str, Any], ...]
    image_location: str
    warnings: tuple[str, ...]
    source_row_count: int
    skipped_source_row_count: int
    skipped_image_count: int

    @property
    def row_counts(self) -> dict[str, int]:
        return {
            "aac_dictionary": len(self.dictionary_rows),
            "aac_image_meta": len(self.image_meta_rows),
            "aac_images": len(self.image_rows),
        }


def _text(value: Any) -> str:
    return str(value or "").strip()


def _bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    normalized = _text(value).lower()
    if normalized in _FALSE_VALUES:
        return False
    if normalized in _TRUE_VALUES:
        return True
    return default


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return [part.strip() for part in raw.split(",") if part.strip()]
        return _string_list(parsed)
    if isinstance(value, dict):
        values: list[str] = []
        for nested in value.values():
            values.extend(_string_list(nested))
        return list(dict.fromkeys(values))
    if isinstance(value, (list, tuple, set)):
        values = []
        for nested in value:
            item = _text(nested)
            if item:
                values.append(item)
        return list(dict.fromkeys(values))
    item = _text(value)
    return [item] if item else []


def _json_array(value: Any) -> str:
    return json.dumps(_string_list(value), ensure_ascii=False, separators=(",", ":"))


def _first_value(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = _text(row.get(key))
        if value:
            return value
    return ""


def _has_person(row: dict[str, Any]) -> bool:
    return _bool(row.get("has_person"), default=True)


def _is_active(row: dict[str, Any]) -> bool:
    return _bool(row.get("is_active"), default=True)


def _canonical_sense_id(row: dict[str, Any]) -> str:
    return _first_value(
        row,
        "canonical_sense_id",
        "canonical_id",
        "requested_canonical_sense_id",
        "sense_id",
        "_word_source_sense_id",
    )


def _source_sense_id(row: dict[str, Any], canonical_sense_id: str) -> str:
    return _first_value(row, "source_sense_id", "_word_source_sense_id", "sense_id") or canonical_sense_id


def image_reference_key(row: dict[str, Any], field_name: str, source_path: str) -> tuple[str, str, str]:
    """Build the stable key shared by the ZIP exporter and MaTalk adapter."""
    row_key = _first_value(row, "row_index", "_word_source_row_id", "id", "sense_id", "_word_source_sense_id")
    return row_key, str(field_name), _text(source_path)


def _source_path_fields(rows: Iterable[dict[str, Any]], selected_fields: list[str] | None) -> list[str]:
    if selected_fields is None:
        names = {str(key) for row in rows for key in row if str(key).endswith("_path")}
    else:
        names = {str(key) for key in selected_fields if str(key).endswith("_path")}
    return sorted(names)


def _explicit_image_url(row: dict[str, Any], field_name: str, source_path_count: int) -> str:
    stem = field_name.removesuffix("_path")
    for key in (f"{stem}_url", f"{stem}_image_url"):
        value = _text(row.get(key))
        if value.startswith(("http://", "https://")):
            return value
    # This supports a future long-form source row without allowing one row-level
    # URL to be copied onto multiple variants accidentally.
    if source_path_count == 1:
        value = _text(row.get("image_url"))
        if value.startswith(("http://", "https://")):
            return value
    return ""


def _cloudflare_urls(db: Session | None, source_paths: set[str]) -> dict[tuple[str, str], str]:
    if db is None or not source_paths:
        return {}
    try:
        records = db.execute(
            select(CloudUpload.source_path, CloudUpload.object_key, CloudUpload.variant)
            .where(
                CloudUpload.status == "uploaded",
                CloudUpload.source_path.in_(sorted(source_paths)),
            )
            .order_by(CloudUpload.updated_at.desc(), CloudUpload.created_at.desc())
        ).all()
    except Exception:  # noqa: BLE001 - export should still work without an upload ledger
        return {}

    base_url = _text(getattr(get_settings(), "cloudflare_r2_public_base_url", "")).rstrip("/")
    if not base_url:
        return {}
    resolved: dict[tuple[str, str], str] = {}
    for source_path, object_key, variant in records:
        source = _text(source_path)
        key = _text(object_key)
        upload_variant = _text(variant)
        if source and key and upload_variant and (source, upload_variant) not in resolved:
            resolved[(source, upload_variant)] = f"{base_url}/{quote(key, safe='/')}"
    return resolved


def _cloudflare_variant(field_name: str) -> str:
    match = _PATH_FIELD_RE.match(str(field_name))
    if match is None:
        return ""
    background = "white_background" if match.group("background") == "white_bg" else "regular"
    return f"{match.group('age')}/{match.group('gender')}/{match.group('skin')}/{background}"


def build_matalk_tables(
    rows: list[dict[str, Any]],
    *,
    selected_fields: list[str] | None = None,
    db: Session | None = None,
    image_location: str = "remote",
    image_references: Mapping[tuple[str, str, str], str] | None = None,
) -> MatalkTables:
    """Translate the app's wide inventory rows into the three MaTalk tables.

    ``image_location`` controls the meaning of ``aac_images.image_url``:

    * ``zip`` uses the exact path of the image inside the generated ZIP.
    * ``remote`` uses a source URL or the public URL generated from the uploaded
      Cloudflare object key.

    The adapter intentionally does not upload images or write Neon. It only
    records the location produced by the surrounding export workflow.
    """

    source_rows = list(rows)
    image_location = str(image_location or "remote").strip().lower()
    if image_location not in {"zip", "remote"}:
        raise ValueError("image_location must be either 'zip' or 'remote'")
    image_references = image_references or {}
    path_fields = _source_path_fields(source_rows, selected_fields)
    source_paths = {
        _text(row.get(field_name))
        for row in source_rows
        for field_name in path_fields
        if _text(row.get(field_name))
    }
    cloudflare_urls = _cloudflare_urls(db, source_paths)

    dictionary_by_source: dict[str, dict[str, Any]] = {}
    meta_by_canonical: dict[str, dict[str, Any]] = {}
    image_by_key: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    warnings: list[str] = []
    warning_keys: set[str] = set()
    missing_url_paths: set[str] = set()
    skipped_source_rows = 0
    skipped_images = 0

    def warn_once(message: str) -> None:
        if message not in warning_keys:
            warning_keys.add(message)
            warnings.append(message)

    for row in source_rows:
        canonical_sense_id = _canonical_sense_id(row)
        source_sense_id = _source_sense_id(row, canonical_sense_id)
        if not canonical_sense_id or not source_sense_id:
            skipped_source_rows += 1
            warn_once("Skipped source rows without a source_sense_id/canonical_sense_id.")
            continue
        if not _text(row.get("canonical_sense_id")):
            warn_once("canonical_sense_id was not present in the app source; sense_id was used as the fallback.")
        if len(canonical_sense_id) != 16:
            warn_once(
                "One or more canonical_sense_id values are not 16 characters; review them before importing into Neon."
            )

        source_word = _first_value(row, "source_word", "_word_source_word", "word")
        normalized_word = _first_value(row, "normalized_word") or source_word.casefold()
        lemmatized_word = _first_value(row, "lemmatized_word", "lemma") or normalized_word
        part_of_speech = _first_value(row, "part_of_speech", "_word_source_part_of_speech", "part_of_sentence")
        source_main_category = _first_value(row, "source_main_category", "main_category", "category")
        fine_tune_categories = row.get("source_fine_tune_categories", row.get("fine_tune_categories", []))
        synonyms = row.get("synonyms", row.get("word_synonyms_for_better_meaning", []))
        active = _is_active(row)
        dictionary_by_source.setdefault(
            source_sense_id,
            {
                "sense_id": source_sense_id,
                "source_word": source_word,
                "normalized_word": normalized_word,
                "lemmatized_word": lemmatized_word,
                "part_of_speech": part_of_speech,
                "source_main_category": source_main_category,
                "source_fine_tune_categories": _json_array(fine_tune_categories),
                "canonical_sense_id": canonical_sense_id,
                "canonical_word": _first_value(row, "canonical_word", "word"),
                "lookup_status": _first_value(row, "lookup_status") or ("active" if active else "inactive"),
                "sense_oxford": _first_value(row, "sense_oxford"),
                "sense_wordnet": _first_value(row, "sense_wordnet"),
                "synonyms": _json_array(synonyms),
            },
        )

        meta_by_canonical.setdefault(
            canonical_sense_id,
            {
                "canonical_sense_id": canonical_sense_id,
                "word": _first_value(row, "canonical_word", "word"),
                "part_of_speech": part_of_speech,
                "category": _first_value(row, "main_category", "source_main_category", "category"),
                "has_person": str(_has_person(row)).lower(),
                "image_score": row.get("image_score") if row.get("image_score") is not None else "",
                "is_active": str(active).lower(),
            },
        )

        row_path_fields = [field_name for field_name in path_fields if _text(row.get(field_name))]
        no_person_backgrounds: set[str] = set()
        for field_name in row_path_fields:
            match = _PATH_FIELD_RE.match(field_name)
            if match is None:
                warn_once(f"Ignored unsupported image path column: {field_name}")
                continue
            source_path = _text(row.get(field_name))
            if not source_path:
                continue
            if _has_person(row):
                age = match.group("age")
                gender = match.group("gender")
                skin = match.group("skin")
            else:
                age = gender = skin = "none"
                if match.group("background") in no_person_backgrounds:
                    continue
                no_person_backgrounds.add(match.group("background"))

            image_url = ""
            if image_location == "zip":
                image_url = _text(image_references.get(image_reference_key(row, field_name, source_path)))
            else:
                image_url = _explicit_image_url(row, field_name, len(row_path_fields))
                if not image_url and source_path.startswith(("http://", "https://")):
                    image_url = source_path
                if not image_url:
                    image_url = cloudflare_urls.get((source_path, _cloudflare_variant(field_name)), "")
            if not image_url:
                skipped_images += 1
                missing_url_paths.add(source_path)
                continue

            image_key = (canonical_sense_id, age, gender, skin, match.group("background"))
            if image_key in image_by_key:
                warn_once("Duplicate image tuple(s) were collapsed to the unique MaTalk key.")
                continue
            image_by_key[image_key] = {
                "canonical_sense_id": canonical_sense_id,
                "age": age,
                "gender": gender,
                "skin": skin,
                "background": match.group("background"),
                "image_url": image_url,
            }

    if missing_url_paths:
        if image_location == "zip":
            warn_once(
                f"Skipped {len(missing_url_paths)} image path(s) without a ZIP-relative location; "
                "the ZIP image index and MaTalk image table could not be linked."
            )
        else:
            warn_once(
                f"Skipped {len(missing_url_paths)} image path(s) without a public URL; upload them to Cloudflare "
                "or provide a URL before importing aac_images into Neon."
            )
    if source_rows and not image_by_key:
        location_label = "a ZIP-relative location" if image_location == "zip" else "a public URL"
        warn_once(f"No aac_images rows were exported because no selected image had {location_label}.")

    return MatalkTables(
        dictionary_rows=tuple(dictionary_by_source.values()),
        image_meta_rows=tuple(meta_by_canonical.values()),
        image_rows=tuple(image_by_key.values()),
        image_location=image_location,
        warnings=tuple(warnings),
        source_row_count=len(source_rows),
        skipped_source_row_count=skipped_source_rows,
        skipped_image_count=skipped_images,
    )


def _write_csv(path: Path, fields: tuple[str, ...], rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def write_matalk_artifacts(export_dir: Path, tables: MatalkTables) -> dict[str, Path]:
    export_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "dictionary": export_dir / MATALK_ARTIFACT_FILENAMES["dictionary"],
        "image_meta": export_dir / MATALK_ARTIFACT_FILENAMES["image_meta"],
        "images": export_dir / MATALK_ARTIFACT_FILENAMES["images"],
        "manifest": export_dir / MATALK_ARTIFACT_FILENAMES["manifest"],
        "readme": export_dir / MATALK_ARTIFACT_FILENAMES["readme"],
    }
    _write_csv(paths["dictionary"], MATALK_DICTIONARY_FIELDS, tables.dictionary_rows)
    _write_csv(paths["image_meta"], MATALK_IMAGE_META_FIELDS, tables.image_meta_rows)
    _write_csv(paths["images"], MATALK_IMAGES_FIELDS, tables.image_rows)

    manifest = {
        "format": "MATALK_v2",
        "table_order": ["aac_dictionary", "aac_image_meta", "aac_images"],
        "files": {
            "aac_dictionary": MATALK_ARTIFACT_FILENAMES["dictionary"],
            "aac_image_meta": MATALK_ARTIFACT_FILENAMES["image_meta"],
            "aac_images": MATALK_ARTIFACT_FILENAMES["images"],
        },
        "row_counts": tables.row_counts,
        "source_row_count": tables.source_row_count,
        "skipped_source_row_count": tables.skipped_source_row_count,
        "skipped_image_count": tables.skipped_image_count,
        "warnings": list(tables.warnings),
        "array_columns_are_json_strings": ["source_fine_tune_categories", "synonyms"],
        "image_key": ["canonical_sense_id", "age", "gender", "skin", "background"],
        "image_reference_mode": "zip_relative_path" if tables.image_location == "zip" else "remote_url",
        "image_url_requirement": (
            "aac_images.image_url is relative to the ZIP root."
            if tables.image_location == "zip"
            else "aac_images.image_url must be a final public remote URL before Neon import."
        ),
    }
    paths["manifest"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["readme"].write_text(
        "# MaTalk AI tables export\n\n"
        "Import the files in this order: `aac_dictionary.csv`, `aac_image_meta.csv`, then `aac_images.csv`.\n\n"
        "The two array columns are JSON strings in the CSV and must be parsed into PostgreSQL `TEXT[]` values. "
        + (
            "In a ZIP export, `aac_images.image_url` is the exact image path relative to the ZIP root. "
            "In a remote export, it is the final public URL including the remote object filename. "
        )
        + "The dictionary and metadata rows are repeat-safe when imported using the documented upsert keys.\n",
        encoding="utf-8",
    )
    return paths
