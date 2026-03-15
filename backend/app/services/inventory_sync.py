from __future__ import annotations

import json
from datetime import datetime
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.inventory_session import inventory_enabled, inventory_engine
from app.inventory_models import BACKGROUND_VALUES, inventory_slot_column_name, word_inventory
from app.models import Asset, CsvJob, CsvJobItem, CsvTaskNode, Entry
from app.services.person_profiles import entry_age_options, entry_gender_options, entry_skin_color_options
from app.services.repository import Repository


class InventorySyncService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = Repository(db)

    def enabled(self) -> bool:
        return inventory_enabled()

    def _expected_slot_names(self, entry: Entry) -> list[str]:
        return [
            inventory_slot_column_name(age, gender, skin_color, background)
            for age in entry_age_options(entry)
            for gender in entry_gender_options(entry)
            for skin_color in entry_skin_color_options(entry)
            for background in BACKGROUND_VALUES
        ]

    def _row_payload(
        self,
        *,
        job: CsvJob,
        item: CsvJobItem,
        entry: Entry,
        tasks: list[CsvTaskNode],
    ) -> dict[str, object]:
        slot_values = {
            column.name: ""
            for column in word_inventory.columns
            if column.name.endswith("_path")
        }
        failures: list[dict[str, str]] = []

        if item.base_regular_asset_id:
            asset = self.repo.get_asset(item.base_regular_asset_id)
            if asset is not None:
                slot_values[inventory_slot_column_name("kid", "male", "white", "regular")] = asset.abs_path
        if item.base_white_bg_asset_id:
            asset = self.repo.get_asset(item.base_white_bg_asset_id)
            if asset is not None:
                slot_values[inventory_slot_column_name("kid", "male", "white", "white_bg")] = asset.abs_path

        for task in tasks:
            profile = str(task.profile_key or "").split(":")
            if len(profile) == 3:
                gender, age, skin_color = profile[0], profile[1], profile[2]
                if task.regular_asset_id:
                    regular = self.repo.get_asset(task.regular_asset_id)
                    if regular is not None:
                        slot_values[inventory_slot_column_name(age, gender, skin_color, "regular")] = regular.abs_path
                if task.white_bg_asset_id:
                    white_bg = self.repo.get_asset(task.white_bg_asset_id)
                    if white_bg is not None:
                        slot_values[inventory_slot_column_name(age, gender, skin_color, "white_bg")] = white_bg.abs_path
            if task.status in {"failed", "canceled"}:
                failures.append(
                    {
                        "task_key": task.task_key,
                        "step_name": task.step_name,
                        "profile_key": task.profile_key,
                        "status": task.status,
                        "error": task.error_summary,
                    }
                )

        expected_slots = self._expected_slot_names(entry)
        missing_slots = [slot for slot in expected_slots if not str(slot_values.get(slot) or "").strip()]
        now = datetime.utcnow()
        return {
            "source_csv_job_id": job.id,
            "source_csv_job_item_id": item.id,
            "source_entry_id": entry.id,
            "source_batch_id": job.batch_id,
            "source_shadow_run_id": item.shadow_run_id or "",
            "word": entry.word,
            "part_of_sentence": entry.part_of_sentence,
            "category": entry.category,
            "context": entry.context,
            "job_status": item.status,
            "fully_complete": item.status == "completed" and not missing_slots and not failures,
            "missing_slots_json": json.dumps(missing_slots, ensure_ascii=True),
            "failure_reasons_json": json.dumps(failures, ensure_ascii=True),
            "synced_at": now,
            "updated_at": now,
            **slot_values,
        }

    def build_export_rows(self, csv_job_id: str) -> list[dict[str, object]]:
        overview = self.repo.csv_job_overview(csv_job_id)
        if overview is None:
            return []
        job = overview["job"]
        items: list[CsvJobItem] = overview["items"]
        tasks: list[CsvTaskNode] = overview["tasks"]
        tasks_by_item: dict[str, list[CsvTaskNode]] = {}
        for task in tasks:
            tasks_by_item.setdefault(task.csv_job_item_id, []).append(task)

        rows: list[dict[str, object]] = []
        for item in items:
            entry = self.repo.get_entry(item.entry_id)
            if entry is None:
                continue
            payload = self._row_payload(job=job, item=item, entry=entry, tasks=tasks_by_item.get(item.id, []))
            export_row: dict[str, object] = {
                "row_index": item.row_index,
                "word": entry.word,
                "part_of_sentence": entry.part_of_sentence,
                "category": entry.category,
                "context": entry.context,
                "job_status": item.status,
                "fully_complete": bool(payload.get("fully_complete")),
                "missing_slots_json": payload.get("missing_slots_json", "[]"),
                "failure_reasons_json": payload.get("failure_reasons_json", "[]"),
            }
            for column in word_inventory.columns:
                name = str(column.name)
                if name.endswith("_path"):
                    export_row[name] = payload.get(name, "")
            rows.append(export_row)
        return rows

    def sync_csv_job(self, csv_job_id: str) -> int:
        if inventory_engine is None:
            return 0
        overview = self.repo.csv_job_overview(csv_job_id)
        if overview is None:
            return 0
        job = overview["job"]
        items: list[CsvJobItem] = overview["items"]
        tasks: list[CsvTaskNode] = overview["tasks"]
        tasks_by_item: dict[str, list[CsvTaskNode]] = {}
        for task in tasks:
            tasks_by_item.setdefault(task.csv_job_item_id, []).append(task)

        synced = 0
        with inventory_engine.begin() as conn:
            for item in items:
                entry = self.repo.get_entry(item.entry_id)
                if entry is None:
                    continue
                payload = self._row_payload(job=job, item=item, entry=entry, tasks=tasks_by_item.get(item.id, []))
                existing = conn.execute(
                    select(word_inventory.c.id, word_inventory.c.created_at).where(
                        word_inventory.c.source_csv_job_item_id == item.id
                    )
                ).first()
                if existing:
                    payload["id"] = existing.id
                    payload["created_at"] = existing.created_at
                    conn.execute(
                        update(word_inventory)
                        .where(word_inventory.c.source_csv_job_item_id == item.id)
                        .values(**payload)
                    )
                else:
                    payload["id"] = f"inv_{uuid4().hex[:24]}"
                    payload["created_at"] = payload["updated_at"]
                    conn.execute(word_inventory.insert().values(**payload))
                synced += 1
        return synced
