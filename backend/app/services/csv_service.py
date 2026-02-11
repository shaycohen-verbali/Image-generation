from __future__ import annotations

import csv
import io
from collections.abc import Iterable


COLUMN_ALIASES = {
    "word": ["word"],
    "part_of_sentence": ["part of sentence", "part_of_sentence", "pos"],
    "category": ["category"],
    "context": ["context"],
    "boy_or_girl": ["boy or girl", "boy_or_girl"],
    "batch": ["batch"],
}


def _norm(value: str) -> str:
    return value.strip().lower()


def _pick(row: dict[str, str], aliases: Iterable[str]) -> str:
    index = {_norm(key): key for key in row.keys()}
    for alias in aliases:
        key = index.get(_norm(alias))
        if key is not None:
            return str(row.get(key, "") or "").strip()
    return ""


def parse_entries_csv(content: bytes) -> list[dict[str, str]]:
    decoded = content.decode("utf-8-sig", errors="ignore")
    reader = csv.DictReader(io.StringIO(decoded))
    rows: list[dict[str, str]] = []

    for row in reader:
        parsed = {
            "word": _pick(row, COLUMN_ALIASES["word"]),
            "part_of_sentence": _pick(row, COLUMN_ALIASES["part_of_sentence"]),
            "category": _pick(row, COLUMN_ALIASES["category"]),
            "context": _pick(row, COLUMN_ALIASES["context"]),
            "boy_or_girl": _pick(row, COLUMN_ALIASES["boy_or_girl"]),
            "batch": _pick(row, COLUMN_ALIASES["batch"]),
        }
        rows.append(parsed)
    return rows


def validate_entry_row(row: dict[str, str]) -> str | None:
    if not row.get("word"):
        return "word is required"
    if not row.get("part_of_sentence"):
        return "part_of_sentence is required"
    return None
