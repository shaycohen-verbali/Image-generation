from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from typing import Any

from sqlalchemy import and_, desc, func, or_, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.inventory_session import inventory_engine
from app.inventory_models import word_inventory
from app.services.storage import materialize_path

settings = get_settings()

SENSE_SLOT_RE = re.compile(r"^(toddler|kid|tween|teenager)_(male|female)_(white|black|asian|brown)_(regular|white_bg)_path$")
MAX_LEMMA_RESULTS = 50
IMAGE_TOKEN_TTL_SECONDS = 15 * 60


class LibraryNotConfigured(RuntimeError):
    pass


def _require_inventory() -> Any:
    if inventory_engine is None:
        raise LibraryNotConfigured("Inventory database is not configured")
    return inventory_engine


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _library_secret() -> bytes:
    configured = _clean(getattr(settings, "supabase_service_role_key", ""))
    fallback = _clean(getattr(settings, "database_url", "")) or "aac-library-image-token"
    return (configured or fallback).encode("utf-8")


def make_image_token(path: str) -> str:
    payload = {"path": _clean(path), "exp": int(time.time()) + IMAGE_TOKEN_TTL_SECONDS}
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii").rstrip("=")
    signature = hmac.new(_library_secret(), raw.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{raw}.{signature}"


def resolve_image_token(token: str) -> str:
    raw, separator, signature = _clean(token).partition(".")
    if not separator or not raw or not signature:
        raise ValueError("Invalid image token")
    expected = hmac.new(_library_secret(), raw.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("Invalid image token")
    padded = raw + "=" * (-len(raw) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid image token") from exc
    if int(payload.get("exp") or 0) < int(time.time()):
        raise ValueError("Image token expired")
    path = _clean(payload.get("path"))
    if not path:
        raise ValueError("Image path missing")
    return path


def _image_columns() -> list[str]:
    return [column.name for column in word_inventory.columns if column.name.endswith("_path")]


def _prompt_column(path_column: str) -> str:
    return path_column[:-5] + "_prompt"


def _slot_profile(path_column: str) -> dict[str, str] | None:
    match = SENSE_SLOT_RE.match(path_column)
    if match is None:
        return None
    age, gender, skin_tone, background = match.groups()
    return {"age": age, "gender": gender, "skin_tone": skin_tone, "background": background}


def _fallback_inventory_rows(conn: Any, lemma: str = "") -> list[dict[str, Any]]:
    query = select(
        word_inventory.c.word,
        word_inventory.c.canonical_word,
        word_inventory.c.part_of_speech,
        word_inventory.c.part_of_sentence,
        word_inventory.c.sense_id,
        word_inventory.c.sense_oxford,
        word_inventory.c.sense_wordnet,
        word_inventory.c.updated_at,
    ).where(word_inventory.c.is_active.is_(True))
    value = _clean(lemma).lower()
    if value:
        pattern = f"%{value}%"
        query = query.where(or_(func.lower(word_inventory.c.word).like(pattern), func.lower(word_inventory.c.canonical_word).like(pattern)))
    query = query.order_by(func.lower(word_inventory.c.word), word_inventory.c.part_of_speech, desc(word_inventory.c.updated_at)).limit(5000)
    return [dict(row._mapping) for row in conn.execute(query)]


def _lookup_rows(conn: Any, *, lemma: str = "", pos: str = "", limit: int = 50) -> list[dict[str, Any]]:
    # aac_word_lookup is a live Supabase table whose useful columns are not
    # created by this application. Keep the read projection narrow and fall
    # back to word_inventory when a development database lacks the projection.
    value = _clean(lemma).lower()
    pattern = f"%{value}%" if value else "%"
    pos_value = _clean(pos).lower()
    sql = text(
        """
        SELECT
          lemmatized_word,
          source_word,
          part_of_speech,
          source_sense_id,
          sense_oxford,
          sense_wordnet,
          canonical_word
        FROM aac_word_lookup
        WHERE COALESCE(lemmatized_word, '') <> ''
          AND LOWER(lemmatized_word) LIKE :pattern
          AND (:pos = '' OR LOWER(COALESCE(part_of_speech, '')) = :pos)
        ORDER BY CASE WHEN LOWER(lemmatized_word) = :exact THEN 0 ELSE 1 END,
                 LOWER(lemmatized_word), LOWER(COALESCE(part_of_speech, ''))
        LIMIT :limit
        """
    )
    try:
        rows = [dict(row._mapping) for row in conn.execute(sql, {"pattern": pattern, "exact": value, "pos": pos_value, "limit": max(1, min(limit, MAX_LEMMA_RESULTS))})]
        return rows
    except SQLAlchemyError:
        return _fallback_inventory_rows(conn, lemma)


def list_lemmas(*, query: str = "", pos: str = "", limit: int = 20, cursor: str = "") -> dict[str, Any]:
    engine = _require_inventory()
    safe_limit = max(1, min(int(limit or 20), MAX_LEMMA_RESULTS))
    with engine.connect() as conn:
        rows = _lookup_rows(conn, lemma=query, pos=pos, limit=min(5000, safe_limit * 8))

        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            lemma = _clean(row.get("lemmatized_word") or row.get("canonical_word") or row.get("word"))
            if not lemma:
                continue
            item = grouped.setdefault(lemma, {"lemma": lemma, "forms": set(), "parts_of_speech": set(), "sense_ids": set(), "image_count": 0})
            form = _clean(row.get("source_word") or row.get("word"))
            if form:
                item["forms"].add(form)
            part = _clean(row.get("part_of_speech") or row.get("part_of_sentence"))
            if part:
                item["parts_of_speech"].add(part)
            sense_id = _clean(row.get("source_sense_id") or row.get("sense_id"))
            if sense_id:
                item["sense_ids"].add(sense_id)

        # Count current V1 paths in one compact inventory query for the lemmas
        # returned above. This avoids sending the 156-column table to the UI.
        if grouped:
            image_columns = _image_columns()
            inventory_query = select(
                word_inventory.c.word,
                word_inventory.c.canonical_word,
                word_inventory.c.sense_id,
                *[word_inventory.c[column] for column in image_columns],
            ).where(word_inventory.c.is_active.is_(True))
            for row in conn.execute(inventory_query):
                mapping = row._mapping
                row_lemmas = {_clean(mapping.get("word")).lower(), _clean(mapping.get("canonical_word")).lower()}
                matches = [name for name in grouped if name.lower() in row_lemmas]
                if not matches:
                    continue
                count = sum(1 for column in image_columns if _clean(mapping.get(column)))
                for name in matches:
                    grouped[name]["image_count"] += count

    result = []
    for item in grouped.values():
        result.append({
            "lemma": item["lemma"],
            "forms": sorted(item["forms"]),
            "parts_of_speech": sorted(item["parts_of_speech"]),
            "sense_count": len(item["sense_ids"]),
            "image_count": item["image_count"],
        })
    result.sort(key=lambda item: (0 if item["lemma"].lower() == _clean(query).lower() else 1, item["lemma"]))
    return {"lemmas": result[:safe_limit], "limit": safe_limit, "cursor": cursor, "next_cursor": ""}


def get_lemma(lemma: str) -> dict[str, Any]:
    engine = _require_inventory()
    requested = _clean(lemma).lower()
    with engine.connect() as conn:
        rows = _lookup_rows(conn, lemma=requested, limit=5000)
        rows = [row for row in rows if _clean(row.get("lemmatized_word") or row.get("canonical_word") or row.get("word")).lower() == requested] or rows
        if not rows:
            rows = _fallback_inventory_rows(conn, requested)

    groups: dict[str, dict[str, Any]] = {}
    forms: set[str] = set()
    for row in rows:
        pos = _clean(row.get("part_of_speech") or row.get("part_of_sentence") or "other") or "other"
        group = groups.setdefault(pos, {"pos": pos, "senses": []})
        form = _clean(row.get("source_word") or row.get("word"))
        if form:
            forms.add(form)
        sense_id = _clean(row.get("source_sense_id") or row.get("sense_id"))
        definition = _clean(row.get("sense_oxford")) or _clean(row.get("sense_wordnet")) or "No definition available"
        if sense_id and not any(sense["id"] == sense_id for sense in group["senses"]):
            group["senses"].append({"id": sense_id, "definition": definition, "sense_oxford": _clean(row.get("sense_oxford")), "sense_wordnet": _clean(row.get("sense_wordnet")), "image_count": 0})

    # Inventory is authoritative for current image counts and may contain
    # senses absent from the lookup projection. Merge those rows by POS/ID.
    with engine.connect() as conn:
        inventory_query = select(
            word_inventory.c.word,
            word_inventory.c.canonical_word,
            word_inventory.c.part_of_speech,
            word_inventory.c.part_of_sentence,
            word_inventory.c.sense_id,
            word_inventory.c.sense_oxford,
            word_inventory.c.sense_wordnet,
            *[word_inventory.c[column] for column in _image_columns()],
        ).where(word_inventory.c.is_active.is_(True))
        for row in conn.execute(inventory_query):
            mapping = row._mapping
            row_lemma = _clean(mapping.get("canonical_word") or mapping.get("word")).lower()
            if row_lemma != requested:
                continue
            forms.add(_clean(mapping.get("word")))
            pos = _clean(mapping.get("part_of_speech") or mapping.get("part_of_sentence") or "other") or "other"
            group = groups.setdefault(pos, {"pos": pos, "senses": []})
            sense_id = _clean(mapping.get("sense_id")) or f"{requested}-{len(group['senses']) + 1}"
            sense = next((item for item in group["senses"] if item["id"] == sense_id), None)
            if sense is None:
                sense = {"id": sense_id, "definition": _clean(mapping.get("sense_oxford")) or _clean(mapping.get("sense_wordnet")) or "No definition available", "image_count": 0}
                group["senses"].append(sense)
            sense["image_count"] += sum(1 for column in _image_columns() if _clean(mapping.get(column)))

    ordered_groups = sorted(groups.values(), key=lambda item: item["pos"])
    return {"lemma": requested, "observed_forms": sorted(forms), "pos_groups": ordered_groups}


def list_sense_images(sense_id: str) -> dict[str, Any]:
    engine = _require_inventory()
    requested = _clean(sense_id)
    if not requested:
        return {"sense_id": "", "images": []}
    path_columns = _image_columns()
    prompt_columns = {_prompt_column(column) for column in path_columns}
    with engine.connect() as conn:
        query = select(word_inventory.c.word, word_inventory.c.part_of_speech, word_inventory.c.sense_id, *[word_inventory.c[column] for column in path_columns], *[word_inventory.c[column] for column in prompt_columns]).where(and_(word_inventory.c.is_active.is_(True), word_inventory.c.sense_id == requested)).order_by(desc(word_inventory.c.updated_at)).limit(1)
        row = conn.execute(query).mappings().first()
    if not row:
        return {"sense_id": requested, "images": []}

    images = []
    for path_column in path_columns:
        path = _clean(row.get(path_column))
        profile = _slot_profile(path_column)
        if not path or profile is None:
            continue
        filename = path.rsplit("/", 1)[-1] or f"{requested}_{path_column}.jpg"
        token = make_image_token(path)
        image_url = f"/api/v1/library/images/{token}"
        images.append({
            "id": f"{requested}:{path_column}",
            **profile,
            "filename": filename,
            "image_url": image_url,
            "original_url": f"{image_url}?download=1",
            "path": path,
            "prompt": _clean(row.get(_prompt_column(path_column))),
            "word": _clean(row.get("word")),
            "part_of_speech": _clean(row.get("part_of_speech")),
        })
    return {"sense_id": requested, "images": images}


def materialize_image(token: str):
    path = resolve_image_token(token)
    return materialize_path(path, cache_namespace="library-images")
