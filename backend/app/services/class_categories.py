"""Keep MaTalk Class upload categories equal to the inventory's main_category.

Uploads made before the uploader read ``main_category`` copied
``word_inventory.category`` (often dictionary text). This refresh runs at
startup: it compares each uploaded sense with its inventory topic and rewrites
only rows that differ, so later startups write nothing.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from sqlalchemy import bindparam, select, update

from app.inventory_models import word_inventory
from app.models import MatalkClassUpload

logger = logging.getLogger(__name__)
_CHUNK = 500


def _topics_by_sense(inventory_engine, sense_ids: list[str]) -> dict[str, str]:
    topics: dict[str, str] = {}
    with inventory_engine.connect() as conn:
        for offset in range(0, len(sense_ids), _CHUNK):
            chunk = sense_ids[offset:offset + _CHUNK]
            rows = conn.execute(
                select(word_inventory.c.sense_id, word_inventory.c.main_category, word_inventory.c.is_active)
                .where(word_inventory.c.sense_id.in_(chunk))
            ).all()
            # Prefer an active row's topic; any non-empty topic beats none.
            for sense_id, topic, active in sorted(rows, key=lambda row: bool(row[2])):
                topic = str(topic or "").strip()
                if topic:
                    topics[str(sense_id)] = topic
    return topics


def refresh_class_upload_categories(ledger_engine, inventory_engine) -> int:
    """Return how many upload rows changed."""
    if inventory_engine is None:
        return 0
    with ledger_engine.connect() as conn:
        current = conn.execute(
            select(MatalkClassUpload.sense_id, MatalkClassUpload.category).distinct()
        ).all()
    if not current:
        return 0
    sense_ids = sorted({str(sense_id) for sense_id, _ in current if sense_id})
    topics = _topics_by_sense(inventory_engine, sense_ids)
    # Group senses by the category they should have, so one statement per
    # topic updates every row of those senses.
    wanted: dict[str, set[str]] = defaultdict(set)
    for sense_id, category in current:
        target = topics.get(str(sense_id), "")
        if str(category or "") != target:
            wanted[target].add(str(sense_id))
    changed = 0
    with ledger_engine.begin() as conn:
        for target, senses in wanted.items():
            ids = sorted(senses)
            for offset in range(0, len(ids), _CHUNK):
                result = conn.execute(
                    update(MatalkClassUpload)
                    .where(MatalkClassUpload.sense_id.in_(bindparam("ids", expanding=True)))
                    .where(MatalkClassUpload.category != target)
                    .values(category=target),
                    {"ids": ids[offset:offset + _CHUNK]},
                )
                changed += result.rowcount or 0
    if changed:
        logger.info("MaTalk Class uploads: refreshed %s categories from main_category", changed)
    return changed


__all__ = ["refresh_class_upload_categories"]
