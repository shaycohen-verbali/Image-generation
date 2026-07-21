from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import func, or_, select

from app.db.inventory_session import inventory_enabled, inventory_engine
from app.inventory_models import word_inventory


APPROVED_WORD_SOURCE_TABLES = {
    "word_inventory": word_inventory,
}


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


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
    ) -> dict[str, Any]:
        normalized, table = self.approved_table(table_name)
        if inventory_engine is None:
            raise RuntimeError("Inventory database is not configured")

        safe_limit = max(1, min(int(limit or 200), 500))
        safe_offset = max(0, int(offset or 0))
        query = select(
            table.c.id,
            table.c.word,
            table.c.part_of_sentence,
            table.c.category,
            table.c.context,
            table.c.job_status,
            table.c.fully_complete,
            table.c.updated_at,
        )
        count_query = select(func.count()).select_from(table)
        search_value = str(search or "").strip()
        if search_value:
            pattern = f"%{search_value}%"
            predicate = or_(
                table.c.word.ilike(pattern),
                table.c.part_of_sentence.ilike(pattern),
                table.c.category.ilike(pattern),
            )
            query = query.where(predicate)
            count_query = count_query.where(predicate)
        query = query.order_by(table.c.word.asc(), table.c.part_of_sentence.asc(), table.c.id.asc())
        query = query.offset(safe_offset).limit(safe_limit)

        with inventory_engine.connect() as conn:
            total = int(conn.execute(count_query).scalar_one() or 0)
            rows = [
                {key: _json_value(value) for key, value in row._mapping.items()}
                for row in conn.execute(query)
            ]
        return {
            "table_name": normalized,
            "rows": rows,
            "total": total,
            "limit": safe_limit,
            "offset": safe_offset,
        }

    def get_rows(self, table_name: str, row_ids: list[str]) -> list[dict[str, Any]]:
        normalized, table = self.approved_table(table_name)
        if inventory_engine is None:
            raise RuntimeError("Inventory database is not configured")
        ids = list(dict.fromkeys(str(row_id or "").strip() for row_id in row_ids if str(row_id or "").strip()))
        if not ids:
            return []
        if len(ids) > 500:
            raise ValueError("At most 500 word-source rows can be imported at once")
        with inventory_engine.connect() as conn:
            found = list(conn.execute(select(table).where(table.c.id.in_(ids))))
        by_id = {str(row._mapping["id"]): dict(row._mapping) for row in found}
        rows: list[dict[str, Any]] = []
        for row_id in ids:
            row = by_id.get(row_id)
            if row is None:
                continue
            rows.append(
                {
                    "word": str(row.get("word") or "").strip(),
                    "part_of_sentence": str(row.get("part_of_sentence") or "").strip(),
                    "category": str(row.get("category") or "").strip(),
                    "context": str(row.get("context") or "").strip(),
                    "_word_source_table": normalized,
                    "_word_source_row_id": row_id,
                }
            )
        return rows
