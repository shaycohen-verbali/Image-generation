from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from sqlalchemy import case, desc, func, or_, select

from app.db.inventory_session import inventory_enabled, inventory_engine
from app.inventory_models import aac_word_lookup, word_inventory


APPROVED_WORD_SOURCE_TABLES = {
    "word_inventory": word_inventory,
}


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _synonyms_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return ""
        try:
            parsed = json.loads(stripped)
        except (TypeError, ValueError):
            return stripped
        return _synonyms_text(parsed)
    if isinstance(value, dict):
        values: list[str] = []
        for nested in value.values():
            text = _synonyms_text(nested)
            if text:
                values.extend(part.strip() for part in text.split(",") if part.strip())
        return ", ".join(dict.fromkeys(values))
    if isinstance(value, (list, tuple, set)):
        values = []
        for nested in value:
            text = _synonyms_text(nested)
            if text:
                values.extend(part.strip() for part in text.split(",") if part.strip())
        return ", ".join(dict.fromkeys(values))
    return str(value).strip()


class WordSourceService:
    def list_sources(self) -> list[dict[str, Any]]:
        enabled = inventory_enabled()
        return [
            {
                "table_name": table_name,
                "label": table_name,
                "available": enabled,
                "readable": True,
                "writable": True,
            }
            for table_name in APPROVED_WORD_SOURCE_TABLES
        ]

    @staticmethod
    def approved_table(table_name: str):
        normalized = str(table_name or "").strip().lower()
        table = APPROVED_WORD_SOURCE_TABLES.get(normalized)
        if table is None:
            raise ValueError(f"Word source table is not approved: {table_name}")
        return normalized, table

    def list_rows(
        self,
        table_name: str,
        *,
        search: str = "",
        limit: int = 200,
        offset: int = 0,
        selection_mode: str = "all",
        row_id: str = "",
        range_start: int | None = None,
        range_end: int | None = None,
        parts_of_speech: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized, table = self.approved_table(table_name)
        if inventory_engine is None:
            raise RuntimeError("Inventory database is not configured")

        safe_limit = max(1, min(int(limit or 200), 500))
        safe_offset = max(0, int(offset or 0))
        selected = self._selection_query(
            table,
            selection_mode=selection_mode,
            row_id=row_id,
            range_start=range_start,
            range_end=range_end,
            parts_of_speech=parts_of_speech,
        ).subquery()
        lookup = aac_word_lookup.alias("word_lookup")
        image_columns = [selected.c[column.name] for column in table.columns if column.name.endswith("_path")]
        has_existing_image = or_(*[func.length(func.trim(column)) > 0 for column in image_columns])
        query = select(
            selected.c.id,
            selected.c.position,
            selected.c.word,
            selected.c.part_of_sentence,
            selected.c.part_of_speech,
            selected.c.sense_id,
            selected.c.sense_wordnet,
            selected.c.sense_oxford,
            lookup.c.synonyms,
            selected.c.category,
            selected.c.context,
            selected.c.job_status,
            selected.c.image_score,
            selected.c.needs_person_attention,
            selected.c.fully_complete,
            case((has_existing_image, True), else_=False).label("has_existing_image"),
            selected.c.updated_at,
        ).select_from(selected.outerjoin(lookup, lookup.c.source_sense_id == selected.c.sense_id))
        count_query = select(func.count()).select_from(selected)
        search_value = str(search or "").strip()
        if search_value:
            pattern = f"%{search_value}%"
            predicate = or_(
                selected.c.word.ilike(pattern),
                selected.c.part_of_speech.ilike(pattern),
                selected.c.sense_id.ilike(pattern),
            )
            query = query.where(predicate)
            count_query = count_query.where(predicate)
        query = query.order_by(selected.c.position.asc())
        query = query.offset(safe_offset).limit(safe_limit)

        with inventory_engine.connect() as conn:
            total = int(conn.execute(count_query).scalar_one() or 0)
            rows = []
            for row in conn.execute(query):
                serialized = {key: _json_value(value) for key, value in row._mapping.items()}
                serialized["sense_wordnet"] = str(serialized.get("sense_wordnet") or "")
                serialized["sense_oxford"] = str(serialized.get("sense_oxford") or "")
                serialized["word_synonyms_for_better_meaning"] = _synonyms_text(serialized.pop("synonyms", None))
                rows.append(serialized)
            pos_values = list(
                conn.execute(
                    select(table.c.part_of_speech)
                    .where(table.c.is_active.is_(True), func.length(func.trim(table.c.part_of_speech)) > 0)
                    .distinct()
                    .order_by(table.c.part_of_speech.asc())
                ).scalars()
            )
        return {
            "table_name": normalized,
            "rows": rows,
            "total": total,
            "limit": safe_limit,
            "offset": safe_offset,
            "parts_of_speech": [str(value) for value in pos_values],
        }

    def _selection_query(
        self,
        table,
        *,
        selection_mode: str,
        row_id: str = "",
        range_start: int | None = None,
        range_end: int | None = None,
        parts_of_speech: list[str] | None = None,
        include_inactive: bool = False,
    ):
        mode = str(selection_mode or "all").strip().lower()
        if mode not in {"single", "range", "all"}:
            raise ValueError(f"Unsupported selection mode: {selection_mode}")
        position = func.row_number().over(
            order_by=(func.lower(table.c.word), table.c.part_of_speech, table.c.sense_id, table.c.id)
        ).label("position")
        # Rank only the identifiers needed for the window sort. The inventory
        # contains many wide prompt/path columns, and sorting all of them was
        # generating large temporary files on the live database.
        ordered_query = select(table.c.id.label("_selection_id"), position)
        if not include_inactive:
            ordered_query = ordered_query.where(table.c.is_active.is_(True))
        ordered = ordered_query.subquery("ordered_word_inventory_ids")
        query = select(*table.c, ordered.c.position).select_from(
            table.join(ordered, ordered.c._selection_id == table.c.id)
        )
        if mode == "single":
            selected_id = str(row_id or "").strip()
            if not selected_id:
                raise ValueError("Choose one exact word row")
            query = query.where(table.c.id == selected_id)
        elif mode == "range":
            start = int(range_start or 0)
            end = int(range_end or 0)
            if start < 1 or end < start:
                raise ValueError("Range end must be greater than or equal to range start")
            query = query.where(ordered.c.position.between(start, end))
        normalized_pos = sorted({str(value or "").strip().lower() for value in (parts_of_speech or []) if str(value or "").strip()})
        if normalized_pos:
            query = query.where(func.lower(table.c.part_of_speech).in_(normalized_pos))
        return query

    def get_rows(
        self,
        table_name: str,
        *,
        selection_mode: str,
        row_id: str = "",
        range_start: int | None = None,
        range_end: int | None = None,
        parts_of_speech: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        normalized, table = self.approved_table(table_name)
        if inventory_engine is None:
            raise RuntimeError("Inventory database is not configured")
        selected = self._selection_query(
            table,
            selection_mode=selection_mode,
            row_id=row_id,
            range_start=range_start,
            range_end=range_end,
            parts_of_speech=parts_of_speech,
        ).subquery()
        lookup = aac_word_lookup.alias("word_lookup")
        path_columns = [selected.c[column.name] for column in table.columns if column.name.endswith("_path")]
        query = (
            select(
                selected.c.id,
                selected.c.position,
                selected.c.word,
                selected.c.part_of_sentence,
                selected.c.part_of_speech,
                selected.c.sense_id,
                selected.c.sense_wordnet,
                selected.c.sense_oxford,
                lookup.c.synonyms,
                *path_columns,
            )
            .select_from(selected.outerjoin(lookup, lookup.c.source_sense_id == selected.c.sense_id))
            .order_by(selected.c.position)
        )
        with inventory_engine.connect() as conn:
            found = list(conn.execute(query))
        rows: list[dict[str, Any]] = []
        for result in found:
            row = dict(result._mapping)
            row_id = str(row.get("id") or "").strip()
            part_of_speech = str(row.get("part_of_speech") or row.get("part_of_sentence") or "").strip()
            sense_id = str(row.get("sense_id") or "").strip()
            word_sense = str(row.get("sense_wordnet") or row.get("sense_oxford") or "").strip()
            synonyms = _synonyms_text(row.get("synonyms"))
            existing_paths = {
                column.name: str(row.get(column.name) or "").strip()
                for column in table.columns
                if column.name.endswith("_path") and str(row.get(column.name) or "").strip()
            }
            rows.append(
                {
                    "word": str(row.get("word") or "").strip(),
                    "part_of_sentence": part_of_speech,
                    "category": word_sense,
                    "sense_id": sense_id,
                    "context": "this word is for an AAC word board",
                    "word_synonyms_for_better_meaning": synonyms,
                    "_word_source_table": normalized,
                    "_word_source_row_id": row_id,
                    "_word_source_word": str(row.get("word") or "").strip(),
                    "_word_source_part_of_speech": part_of_speech,
                    "_word_source_sense_id": sense_id,
                    # Carry a compact inventory snapshot into DAG construction. This avoids
                    # dozens of Supabase lookups per imported word when checking dependencies.
                    "_word_source_existing_paths": existing_paths,
                }
            )
        return rows

    def get_export_rows(
        self,
        table_name: str,
        *,
        selection_mode: str,
        row_id: str = "",
        range_start: int | None = None,
        range_end: int | None = None,
        include_inactive: bool = False,
    ) -> list[dict[str, Any]]:
        """Return inventory rows in the shape consumed by the package exporter."""
        normalized, table = self.approved_table(table_name)
        if inventory_engine is None:
            raise RuntimeError("Inventory database is not configured")

        mode = str(selection_mode or "last_job").strip().lower()
        if mode == "last_job":
            with inventory_engine.connect() as conn:
                latest_job_id = conn.execute(
                    select(table.c.source_csv_job_id)
                    .where(
                        *(
                            [table.c.is_active.is_(True)]
                            if not include_inactive
                            else []
                        ),
                        func.length(func.trim(table.c.source_csv_job_id)) > 0,
                    )
                    .order_by(desc(table.c.updated_at), desc(table.c.created_at))
                    .limit(1)
                ).scalar_one_or_none()
                if not latest_job_id:
                    return []
                selected_query = select(*table.c).where(table.c.source_csv_job_id == latest_job_id)
                if not include_inactive:
                    selected_query = selected_query.where(table.c.is_active.is_(True))
                selected = selected_query.subquery()
                lookup = aac_word_lookup.alias("word_lookup_export")
                found = conn.execute(
                    select(selected, lookup.c.synonyms.label("source_synonyms"))
                    .select_from(selected.outerjoin(lookup, lookup.c.source_sense_id == selected.c.sense_id))
                    .order_by(func.lower(selected.c.word), selected.c.part_of_speech, selected.c.id)
                ).mappings()
                return [self._export_row(dict(row), position=index + 1, table_name=normalized) for index, row in enumerate(found)]

        selected = self._selection_query(
            table,
            selection_mode=mode,
            row_id=row_id,
            range_start=range_start,
            range_end=range_end,
            include_inactive=include_inactive,
        ).subquery()
        with inventory_engine.connect() as conn:
            lookup = aac_word_lookup.alias("word_lookup_export")
            found = conn.execute(
                select(selected, lookup.c.synonyms.label("source_synonyms"))
                .select_from(selected.outerjoin(lookup, lookup.c.source_sense_id == selected.c.sense_id))
                .order_by(selected.c.position)
            ).mappings()
            return [self._export_row(dict(row), position=int(row.get("position") or 0), table_name=normalized) for row in found]

    @staticmethod
    def _export_row(row: dict[str, Any], *, position: int, table_name: str) -> dict[str, Any]:
        part_of_speech = str(row.get("part_of_speech") or row.get("part_of_sentence") or "")
        return {
            **row,
            "synonyms": row.get("source_synonyms", row.get("synonyms", "")),
            "row_index": position,
            "part_of_sentence": part_of_speech,
            "category": str(row.get("category") or row.get("sense_wordnet") or row.get("sense_oxford") or ""),
            "context": str(row.get("context") or ""),
            "_word_source_table": table_name,
            "_word_source_row_id": str(row.get("id") or ""),
            "_word_source_word": str(row.get("word") or ""),
            "_word_source_part_of_speech": part_of_speech,
            "_word_source_sense_id": str(row.get("sense_id") or ""),
            "_word_source_existing_paths": {
                key: str(value or "")
                for key, value in row.items()
                if key.endswith("_path") and str(value or "").strip()
            },
        }
