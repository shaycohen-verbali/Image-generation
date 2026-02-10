from __future__ import annotations

import hashlib
import json
import re
from typing import Any


def deterministic_entry_id(word: str, part_of_sentence: str, category: str) -> str:
    key = f"{word.strip().lower()}|{part_of_sentence.strip().lower()}|{category.strip().lower()}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return f"ent_{digest}"


def source_row_hash(payload: dict[str, Any]) -> str:
    normalized = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def sanitize_filename(name: str) -> str:
    value = name or "file"
    value = re.sub(r"[\\/:*?\"<>|]", "_", value)
    value = re.sub(r"\s+", "_", value).strip("._")
    return value[:180] or "file"


def parse_json_relaxed(content: str) -> dict[str, Any]:
    text = content.strip()
    candidates = [text]
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1).strip())
    obj_match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if obj_match:
        candidates.append(obj_match.group(0))

    for candidate in candidates:
        try:
            result = json.loads(candidate)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            continue
    return {}
