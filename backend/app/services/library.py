from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from collections import defaultdict
from typing import Any

from sqlalchemy import and_, case, desc, func, or_, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.inventory_session import inventory_engine
from app.inventory_models import word_inventory
from app.services.storage import materialize_path

settings = get_settings()

SENSE_SLOT_RE = re.compile(r"^(toddler|kid|tween|teenager)_(male|female)_(white|black|asian|brown)_(regular|white_bg)_path$")
MAX_LEMMA_RESULTS = 50
MAX_LOOKUP_ROWS = 50_000
MAX_INVENTORY_ROWS = 50_000
MAX_IMAGE_ROWS = 10_000
IMAGE_TOKEN_TTL_SECONDS = 15 * 60

# These are the values currently present in the live source tables. The
# aliases keep the read API tolerant of older imports that used abbreviations.
LIVE_POS_VALUES = (
    "noun",
    "verb",
    "adjective",
    "adverb",
    "preposition",
    "pronoun",
    "number",
    "exclamation",
    "determiner",
    "conjunction",
    "auxiliary",
    "interjection",
    "modal verb",
    "contraction",
    "infinitive marker",
    "linking verb",
)
POS_ALIASES = {
    "n": "noun",
    "v": "verb",
    "adj": "adjective",
    "adv": "adverb",
    "prep": "preposition",
    "pron": "pronoun",
    "num": "number",
    "excl": "exclamation",
    "det": "determiner",
    "conj": "conjunction",
    "aux": "auxiliary",
    "intj": "interjection",
    "modal": "modal verb",
    "infinitive": "infinitive marker",
    "linking": "linking verb",
}


class LibraryNotConfigured(RuntimeError):
    pass


def _require_inventory() -> Any:
    if inventory_engine is None:
        raise LibraryNotConfigured("Inventory database is not configured")
    return inventory_engine


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _normalise_pos(value: Any) -> str:
    normalized = " ".join(_clean(value).casefold().replace("_", " ").split())
    return POS_ALIASES.get(normalized, normalized)


def _pos_values(value: Any) -> set[str]:
    normalized = _normalise_pos(value)
    if not normalized:
        return set()
    values = {normalized}
    values.update(alias for alias, target in POS_ALIASES.items() if target == normalized)
    return values


def _pos_predicate(columns: tuple[Any, ...], value: Any):
    values = _pos_values(value)
    if not values:
        return None
    predicates = [func.lower(func.trim(column)).in_(sorted(values)) for column in columns]
    return or_(*predicates)


def _escape_like(value: str) -> str:
    """Escape user text before placing it inside a literal LIKE pattern."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _inventory_word_match(value: str):
    normalized = _clean(value).casefold()
    pattern = f"%{_escape_like(normalized)}%"
    return func.lower(func.coalesce(word_inventory.c.word, "")).like(pattern, escape="\\")


def _library_secret() -> bytes:
    configured = _clean(getattr(settings, "supabase_service_role_key", ""))
    fallback = _clean(getattr(settings, "database_url", "")) or "aac-library-image-token"
    return (configured or fallback).encode("utf-8")


def make_image_token(path: str, *, version: str = "") -> str:
    payload = {
        "path": _clean(path),
        "version": _clean(version),
        "exp": int(time.time()) + IMAGE_TOKEN_TTL_SECONDS,
    }
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii").rstrip("=")
    signature = hmac.new(_library_secret(), raw.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{raw}.{signature}"


def _resolve_image_token_payload(token: str) -> dict[str, str]:
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
    return {"path": path, "version": _clean(payload.get("version"))}


def resolve_image_token(token: str) -> str:
    return _resolve_image_token_payload(token)["path"]


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


def _fallback_inventory_rows(conn: Any, lemma: str = "", pos: str = "") -> list[dict[str, Any]]:
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
        query = query.where(_inventory_word_match(value))
    pos_filter = _pos_predicate((word_inventory.c.part_of_speech, word_inventory.c.part_of_sentence), pos)
    if pos_filter is not None:
        query = query.where(pos_filter)
    query = query.order_by(func.lower(word_inventory.c.word), word_inventory.c.part_of_speech, desc(word_inventory.c.updated_at)).limit(MAX_INVENTORY_ROWS)
    return [dict(row._mapping) for row in conn.execute(query)]


def _lookup_rows(conn: Any, *, lemma: str = "", pos: str = "", limit: int = 50) -> list[dict[str, Any]]:
    # aac_word_lookup is a live Supabase table whose useful columns are not
    # created by this application. Keep the read projection narrow and fall
    # back to word_inventory when a development database lacks the projection.
    value = _clean(lemma).casefold()
    escaped = _escape_like(value)
    pattern = f"%{escaped}%" if value else "%"
    prefix = f"{escaped}%" if value else "%"
    pos_values = sorted(_pos_values(pos))
    pos_clause = ""
    params: dict[str, Any] = {
        "pattern": pattern,
        "prefix": prefix,
        "exact": value,
        "escape": "\\",
        "limit": max(1, min(int(limit or 50), MAX_LOOKUP_ROWS)),
    }
    if pos_values:
        placeholders = []
        for index, pos_value in enumerate(pos_values):
            key = f"pos_{index}"
            placeholders.append(f":{key}")
            params[key] = pos_value
        pos_clause = f"AND LOWER(COALESCE(part_of_speech, '')) IN ({', '.join(placeholders)})"
    sql = text(
        f"""
        SELECT
          lemmatized_word,
          source_word,
          part_of_speech,
          source_sense_id,
          source_sense_oxford AS sense_oxford,
          source_sense_wordnet AS sense_wordnet,
          canonical_word,
          canonical_sense_id
        FROM aac_word_lookup
        WHERE lookup_status = 'active'
          AND COALESCE(lemmatized_word, '') <> ''
          AND LOWER(lemmatized_word) LIKE :pattern ESCAPE :escape
          {pos_clause}
        ORDER BY CASE WHEN LOWER(lemmatized_word) = :exact THEN 0
                      WHEN LOWER(lemmatized_word) LIKE :prefix ESCAPE :escape THEN 1
                      ELSE 2 END,
                 LOWER(lemmatized_word), LOWER(COALESCE(part_of_speech, ''))
        LIMIT :limit
        """
    )
    try:
        rows = [dict(row._mapping) for row in conn.execute(sql, params)]
        return rows
    except SQLAlchemyError:
        return _fallback_inventory_rows(conn, lemma, pos)


def _inventory_search_rows(conn: Any, *, lemma: str = "", pos: str = "", limit: int = MAX_INVENTORY_ROWS) -> list[dict[str, Any]]:
    """Return the narrow inventory projection used to decide what is searchable.

    The inventory is intentionally queried independently of the dictionary
    lookup. A lookup row can be missing or have a different canonical form,
    but an active inventory word must still be discoverable in the Library.
    """
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
    value = _clean(lemma).casefold()
    if value:
        query = query.where(_inventory_word_match(value))
    pos_filter = _pos_predicate((word_inventory.c.part_of_speech, word_inventory.c.part_of_sentence), pos)
    if pos_filter is not None:
        query = query.where(pos_filter)

    escaped = _escape_like(value)
    prefix = f"{escaped}%" if value else "%"
    exact_rank = case(
        (func.lower(func.coalesce(word_inventory.c.word, "")) == value, 0),
        (func.lower(func.coalesce(word_inventory.c.word, "")).like(prefix, escape="\\"), 1),
        else_=2,
    )
    query = query.order_by(exact_rank, func.lower(word_inventory.c.word), word_inventory.c.part_of_speech, desc(word_inventory.c.updated_at)).limit(max(1, min(int(limit or MAX_INVENTORY_ROWS), MAX_INVENTORY_ROWS)))
    return [dict(row._mapping) for row in conn.execute(query)]


def _inventory_detail_rows(conn: Any, *, lemma: str = "", sense_ids: set[str] | None = None, words: set[str] | None = None, include_prompts: bool = False) -> list[dict[str, Any]]:
    """Load only the wide path/prompt columns needed for a selected context."""
    path_columns = _image_columns()
    prompt_columns = sorted({_prompt_column(column) for column in path_columns}) if include_prompts else []
    predicates = []
    normalized_sense_ids = {_clean(value) for value in (sense_ids or set()) if _clean(value)}
    normalized_words = {_clean(value).casefold() for value in (words or set()) if _clean(value)}
    if lemma:
        normalized_words.add(_clean(lemma).casefold())
    if normalized_sense_ids:
        predicates.append(word_inventory.c.sense_id.in_(sorted(normalized_sense_ids)))
    if normalized_words:
        predicates.extend([
            func.lower(func.coalesce(word_inventory.c.word, "")).in_(sorted(normalized_words)),
            func.lower(func.coalesce(word_inventory.c.canonical_word, "")).in_(sorted(normalized_words)),
        ])
    if not predicates:
        return []
    query = select(
        word_inventory.c.word,
        word_inventory.c.canonical_word,
        word_inventory.c.part_of_speech,
        word_inventory.c.part_of_sentence,
        word_inventory.c.sense_id,
        word_inventory.c.sense_oxford,
        word_inventory.c.sense_wordnet,
        word_inventory.c.updated_at,
        *[word_inventory.c[column] for column in path_columns],
        *[word_inventory.c[column] for column in prompt_columns],
    ).where(and_(word_inventory.c.is_active.is_(True), or_(*predicates))).order_by(desc(word_inventory.c.updated_at)).limit(MAX_IMAGE_ROWS)
    return [dict(row._mapping) for row in conn.execute(query)]


def _image_count_for_context(
    rows: list[dict[str, Any]],
    *,
    sense_id: str,
    canonical_sense_ids: set[str],
    canonical_words: set[str],
    source_words: set[str] | None = None,
    path_columns: list[str],
) -> int:
    source_sense = _clean(sense_id).casefold()
    target_senses = {source_sense, *{_clean(value).casefold() for value in canonical_sense_ids if _clean(value)}}
    target_words = {_clean(value).casefold() for value in canonical_words if _clean(value)}
    source_word_values = {_clean(value).casefold() for value in (source_words or set()) if _clean(value)}
    matched: set[tuple[str, str]] = set()
    for row in rows:
        row_sense = _clean(row.get("sense_id")).casefold()
        row_words = {_clean(row.get("word")).casefold(), _clean(row.get("canonical_word")).casefold()}
        if row_sense in target_senses:
            pass
        elif target_words & row_words:
            pass
        elif not row_sense and source_word_values & row_words:
            pass
        else:
            continue
        matched.update((column, _clean(row.get(column))) for column in path_columns if _clean(row.get(column)))
    return len(matched)


def list_lemmas(*, query: str = "", pos: str = "", limit: int = 20, cursor: str = "") -> dict[str, Any]:
    engine = _require_inventory()
    safe_limit = max(1, min(int(limit or 20), MAX_LEMMA_RESULTS))
    with engine.connect() as conn:
        # Use both sources: inventory decides whether a word is available in
        # the Library, while the lookup enriches it with inflections and the
        # canonical image target. This also keeps punctuation-only searches
        # literal (for example, `100%` is not treated as a SQL wildcard).
        search_limit = MAX_LOOKUP_ROWS if _clean(query) else min(MAX_LOOKUP_ROWS, safe_limit * 8)
        inventory_rows = _inventory_search_rows(conn, lemma=query, pos=pos, limit=min(MAX_INVENTORY_ROWS, search_limit))
        inventory_words = {
            _clean(row.get("word")).casefold()
            for row in inventory_rows
            if _clean(row.get("word"))
        }
        lookup_rows = _lookup_rows(conn, lemma=query, pos=pos, limit=search_limit)
        lookup_rows = [
            row for row in lookup_rows
            if inventory_words & {
                _clean(row.get("source_word")).casefold(),
                _clean(row.get("lemmatized_word")).casefold(),
                _clean(row.get("canonical_word")).casefold(),
            }
        ]
        rows = lookup_rows + inventory_rows

        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            from_inventory = "lemmatized_word" not in row
            lemma = _clean(row.get("word") if from_inventory else (row.get("lemmatized_word") or row.get("word") or row.get("canonical_word")))
            if not lemma:
                continue
            item = grouped.setdefault(lemma, {"lemma": lemma, "forms": set(), "parts_of_speech": set(), "sense_ids": set(), "image_keys": set(), "image_count": 0})
            form = _clean(row.get("source_word") or row.get("word"))
            if form:
                item["forms"].add(form)
            part = _clean(row.get("part_of_speech") or row.get("part_of_sentence"))
            if part:
                item["parts_of_speech"].add(part)
            sense_id = _clean(row.get("source_sense_id") or row.get("sense_id"))
            if sense_id:
                item["sense_ids"].add(sense_id)
            for image_key in (lemma, row.get("word"), row.get("canonical_word"), row.get("source_word")):
                cleaned_key = _clean(image_key)
                if cleaned_key:
                    item["image_keys"].add(cleaned_key.casefold())

        query_value = _clean(query).casefold()
        def result_rank(name: str) -> tuple[int, str]:
            normalized_name = name.casefold()
            if query_value and normalized_name == query_value:
                rank = 0
            elif query_value and normalized_name.startswith(query_value):
                rank = 1
            elif query_value:
                rank = 2
            else:
                rank = 0
            return rank, normalized_name

        ordered_names = sorted(grouped, key=result_rank)
        visible_names = ordered_names[:safe_limit]

        # Count current V1 paths only for the lemmas that will be returned.
        # A canonical target is included in image_keys, so a source word with
        # no own image still reports its canonical images without loading the
        # wide inventory table for every search match.
        if visible_names:
            image_columns = _image_columns()
            all_keys = set().union(*(grouped[name]["image_keys"] for name in visible_names))
            image_rows = _inventory_detail_rows(conn, words=all_keys)
            for row in image_rows:
                row_keys = {_clean(row.get("word")).casefold(), _clean(row.get("canonical_word")).casefold()}
                for name in visible_names:
                    item = grouped[name]
                    if not (item["image_keys"] & row_keys):
                        continue
                    item["image_slots"] = item.get("image_slots", set())
                    item["image_slots"].update((column, _clean(row.get(column))) for column in image_columns if _clean(row.get(column)))

    result = []
    for name in visible_names:
        item = grouped[name]
        result.append({
            "lemma": item["lemma"],
            "forms": sorted(item["forms"]),
            "parts_of_speech": sorted(item["parts_of_speech"]),
            "sense_count": len(item["sense_ids"]),
            "image_count": len(item.get("image_slots", set())),
        })

    return {"lemmas": result, "limit": safe_limit, "cursor": cursor, "next_cursor": ""}


def get_lemma(lemma: str) -> dict[str, Any]:
    engine = _require_inventory()
    requested = _clean(lemma).casefold()
    with engine.connect() as conn:
        lookup_rows = _lookup_rows(conn, lemma=requested, limit=MAX_LOOKUP_ROWS)
        exact_lookup_rows = [
            row for row in lookup_rows
            if requested in {
                _clean(row.get("lemmatized_word")).casefold(),
                _clean(row.get("source_word")).casefold(),
                _clean(row.get("canonical_word")).casefold(),
            }
        ]
        inventory_rows = _inventory_search_rows(conn, lemma=requested, limit=MAX_INVENTORY_ROWS)
        inventory_rows = [
            row for row in inventory_rows
            if requested in {_clean(row.get("word")).casefold(), _clean(row.get("canonical_word")).casefold()}
        ]
        inventory_words = {_clean(row.get("word")).casefold() for row in inventory_rows if _clean(row.get("word"))}
        exact_lookup_rows = [
            row for row in exact_lookup_rows
            if inventory_words & {
                _clean(row.get("source_word")).casefold(),
                _clean(row.get("lemmatized_word")).casefold(),
                _clean(row.get("canonical_word")).casefold(),
            }
        ]
        rows = exact_lookup_rows if exact_lookup_rows else inventory_rows

        # Load path columns only for this lemma's source/canonical words. The
        # wide inventory table never leaves the server.
        detail_rows = _inventory_detail_rows(
            conn,
            words={requested},
            sense_ids={_clean(row.get("source_sense_id") or row.get("sense_id")) for row in rows if _clean(row.get("source_sense_id") or row.get("sense_id"))},
        )

    groups: dict[str, dict[str, Any]] = {}
    forms: set[str] = set()
    source_sense_words: dict[str, set[str]] = defaultdict(set)
    canonical_words_by_sense: dict[str, set[str]] = defaultdict(set)
    canonical_senses_by_sense: dict[str, set[str]] = defaultdict(set)

    def add_sense(group: dict[str, Any], sense_id: str, definition: str, oxford: str = "", wordnet: str = "") -> None:
        if not sense_id:
            return
        sense = next((item for item in group["senses"] if item["id"] == sense_id), None)
        if sense is None:
            sense = {
                "id": sense_id,
                "definition": definition or "No definition available",
                "sense_oxford": oxford,
                "sense_wordnet": wordnet,
                "image_count": 0,
            }
            group["senses"].append(sense)
        elif sense.get("definition") == "No definition available" and definition:
            sense["definition"] = definition
            sense["sense_oxford"] = oxford
            sense["sense_wordnet"] = wordnet

    for row in rows:
        pos = _clean(row.get("part_of_speech") or row.get("part_of_sentence") or "other") or "other"
        group = groups.setdefault(pos, {"pos": pos, "senses": []})
        form = _clean(row.get("source_word") or row.get("word"))
        if form:
            forms.add(form)
        canonical_word = _clean(row.get("canonical_word"))
        if canonical_word:
            forms.add(canonical_word)
        sense_id = _clean(row.get("source_sense_id") or row.get("sense_id"))
        definition = _clean(row.get("sense_oxford")) or _clean(row.get("sense_wordnet")) or "No definition available"
        add_sense(group, sense_id, definition, _clean(row.get("sense_oxford")), _clean(row.get("sense_wordnet")))
        if sense_id:
            source_sense_words[sense_id].update(value for value in (form, _clean(row.get("word"))) if value)
            if canonical_word and canonical_word.casefold() != requested:
                canonical_words_by_sense[sense_id].add(canonical_word)
            canonical_sense_id = _clean(row.get("canonical_sense_id"))
            if canonical_sense_id and canonical_sense_id != sense_id:
                canonical_senses_by_sense[sense_id].add(canonical_sense_id)

    # Inventory is authoritative for current image counts and may contain
    # senses absent from the lookup projection. Merge those rows by POS/ID.
    for mapping in detail_rows:
        row_lemma = {_clean(mapping.get("word")).casefold(), _clean(mapping.get("canonical_word")).casefold()}
        if requested not in row_lemma:
            continue
        forms.add(_clean(mapping.get("word")))
        canonical_word = _clean(mapping.get("canonical_word"))
        if canonical_word:
            forms.add(canonical_word)
        pos = _clean(mapping.get("part_of_speech") or mapping.get("part_of_sentence") or "other") or "other"
        group = groups.setdefault(pos, {"pos": pos, "senses": []})
        sense_id = _clean(mapping.get("sense_id")) or f"{requested}-{len(group['senses']) + 1}"
        add_sense(
            group,
            sense_id,
            _clean(mapping.get("sense_oxford")) or _clean(mapping.get("sense_wordnet")),
            _clean(mapping.get("sense_oxford")),
            _clean(mapping.get("sense_wordnet")),
        )
        source_sense_words[sense_id].add(_clean(mapping.get("word")))
        if canonical_word and canonical_word.casefold() != requested:
            canonical_words_by_sense[sense_id].add(canonical_word)

    # Fill image counts from the source sense plus its canonical sense/word.
    # The source row wins when a slot exists; canonical rows fill only missing
    # slots in the image endpoint below, while counts use the same union.
    source_sense_ids = set(source_sense_words)
    all_image_words = {requested}
    for values in canonical_words_by_sense.values():
        all_image_words.update(values)
    all_image_senses = source_sense_ids | set().union(*canonical_senses_by_sense.values()) if canonical_senses_by_sense else source_sense_ids
    with engine.connect() as conn:
        count_rows = _inventory_detail_rows(conn, words=all_image_words, sense_ids=all_image_senses)
    path_columns = _image_columns()
    counts = {
        sense_id: _image_count_for_context(
            count_rows,
            sense_id=sense_id,
            canonical_sense_ids=canonical_senses_by_sense.get(sense_id, set()),
            canonical_words=canonical_words_by_sense.get(sense_id, set()),
            source_words=source_sense_words.get(sense_id, set()),
            path_columns=path_columns,
        )
        for sense_id in source_sense_ids
    }
    for group in groups.values():
        for sense in group["senses"]:
            sense["image_count"] = counts.get(sense["id"], 0)

    ordered_groups = sorted(groups.values(), key=lambda item: item["pos"])
    return {"lemma": requested, "observed_forms": sorted(forms), "pos_groups": ordered_groups}


def _lookup_rows_for_sense_ids(conn: Any, sense_ids: set[str]) -> list[dict[str, Any]]:
    values = sorted({_clean(value) for value in sense_ids if _clean(value)})
    if not values:
        return []
    placeholders = ", ".join(f":sense_{index}" for index in range(len(values)))
    params = {f"sense_{index}": value for index, value in enumerate(values)}
    try:
        rows = conn.execute(
            text(
                f"""
                SELECT source_sense_id, source_word, lemmatized_word, part_of_speech,
                       source_sense_oxford AS sense_oxford,
                       source_sense_wordnet AS sense_wordnet,
                       canonical_word, canonical_sense_id
                FROM aac_word_lookup
                WHERE lookup_status = 'active'
                  AND source_sense_id IN ({placeholders})
                """
            ),
            params,
        )
        return [dict(row._mapping) for row in rows]
    except SQLAlchemyError:
        return []


def list_sense_images(sense_id: str) -> dict[str, Any]:
    engine = _require_inventory()
    requested = _clean(sense_id)
    if not requested:
        return {"sense_id": "", "images": []}
    path_columns = _image_columns()
    with engine.connect() as conn:
        source_rows = _inventory_detail_rows(conn, sense_ids={requested}, include_prompts=True)
        lookup_rows = _lookup_rows_for_sense_ids(conn, {requested})
        canonical_sense_ids = {
            _clean(row.get("canonical_sense_id"))
            for row in lookup_rows
            if _clean(row.get("canonical_sense_id")) and _clean(row.get("canonical_sense_id")) != requested
        }
        canonical_words = {
            _clean(row.get("canonical_word"))
            for row in lookup_rows
            if _clean(row.get("canonical_word"))
            and _clean(row.get("canonical_word")).casefold() != _clean(row.get("source_word") or "").casefold()
        }
        for row in source_rows:
            value = _clean(row.get("canonical_word"))
            if value and value.casefold() != _clean(row.get("word")).casefold():
                canonical_words.add(value)

        related_rows = _inventory_detail_rows(
            conn,
            sense_ids={requested, *canonical_sense_ids},
            words=canonical_words,
            include_prompts=True,
        )

    if not source_rows and not related_rows:
        return {"sense_id": requested, "images": []}

    canonical_sense_rows = [
        row for row in related_rows
        if _clean(row.get("sense_id")) in canonical_sense_ids
        and _clean(row.get("sense_id")) != requested
    ]
    canonical_word_values = {value.casefold() for value in canonical_words}
    canonical_word_rows = [
        row for row in related_rows
        if _clean(row.get("sense_id")) not in {requested, *canonical_sense_ids}
        and (
            _clean(row.get("word")).casefold() in canonical_word_values
            or _clean(row.get("canonical_word")).casefold() in canonical_word_values
        )
    ]
    ordered_rows: list[tuple[dict[str, Any], bool]] = [
        *((row, False) for row in source_rows),
        *((row, True) for row in canonical_sense_rows),
        *((row, True) for row in canonical_word_rows),
    ]
    merged_slots: dict[str, tuple[dict[str, Any], bool]] = {}
    for row, inherited in ordered_rows:
        for path_column in path_columns:
            if _clean(row.get(path_column)) and path_column not in merged_slots:
                merged_slots[path_column] = (row, inherited)

    images = []
    for path_column in path_columns:
        slot = merged_slots.get(path_column)
        if slot is None:
            continue
        row, inherited = slot
        path = _clean(row.get(path_column))
        profile = _slot_profile(path_column)
        if not path or profile is None:
            continue
        filename = path.rsplit("/", 1)[-1] or f"{requested}_{path_column}.jpg"
        row_version = _clean(row.get("updated_at"))
        token = make_image_token(path, version=row_version)
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
            "canonical_word": _clean(row.get("canonical_word")),
            "part_of_speech": _clean(row.get("part_of_speech")),
            "image_source": "canonical" if inherited else "inventory",
        })
    return {"sense_id": requested, "images": images}


def materialize_image(token: str):
    payload = _resolve_image_token_payload(token)
    version_key = hashlib.sha256(
        f"{payload['path']}:{payload['version']}".encode("utf-8")
    ).hexdigest()[:20]
    return materialize_path(payload["path"], cache_namespace=f"library-images-{version_key}")
