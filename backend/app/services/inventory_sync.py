from __future__ import annotations

import json
from datetime import datetime
from uuid import uuid4

from sqlalchemy import and_, desc, select, update
from sqlalchemy.orm import Session

from app.db.inventory_session import inventory_enabled, inventory_engine
from app.inventory_models import (
    AGE_VALUES,
    BACKGROUND_VALUES,
    GENDER_VALUES,
    SKIN_VALUES,
    inventory_prompt_column_name,
    inventory_slot_column_name,
    word_inventory,
)
from app.models import Asset, CsvJob, CsvJobItem, CsvTaskNode, Entry
from app.services.repository import Repository

CSV_JOB_EXPORT_BASE_FIELD_SPECS: tuple[dict[str, str], ...] = (
    {"key": "row_index", "label": "Row index"},
    {"key": "word", "label": "Word"},
    {"key": "part_of_sentence", "label": "Part of sentence"},
    {"key": "category", "label": "Category"},
    {"key": "context", "label": "Context"},
    {"key": "job_status", "label": "Job status"},
    {"key": "fully_complete", "label": "Fully complete"},
    {"key": "missing_slots_json", "label": "Missing slots"},
    {"key": "failure_reasons_json", "label": "Failure reasons"},
)


def csv_job_export_field_specs() -> list[dict[str, str]]:
    specs = list(CSV_JOB_EXPORT_BASE_FIELD_SPECS)
    for column in word_inventory.columns:
        name = str(column.name)
        if name.endswith("_path") or name.endswith("_prompt"):
            specs.append({"key": name, "label": name.replace("_", " ")})
    return specs


def normalize_csv_job_export_fields(raw_fields: list[str] | None) -> list[str]:
    allowed = {spec["key"] for spec in csv_job_export_field_specs()}
    if not isinstance(raw_fields, list):
        return [spec["key"] for spec in csv_job_export_field_specs()]

    selected: list[str] = []
    seen: set[str] = set()
    for raw in raw_fields:
        key = str(raw or "").strip()
        if not key or key in seen or key not in allowed:
            continue
        selected.append(key)
        seen.add(key)
    return selected or [spec["key"] for spec in csv_job_export_field_specs()]


class InventorySyncService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = Repository(db)

    def enabled(self) -> bool:
        return inventory_enabled()

    def _prompt_for_stage(self, *, run_id: str, stage_name: str, attempt: int = 0):
        prompt = None
        if attempt > 0:
            prompt = self.repo.get_prompt_for_stage_attempt(run_id=run_id, stage_name=stage_name, attempt=attempt)
        if prompt is None:
            prompt = self.repo.get_latest_prompt_for_stage(run_id=run_id, stage_name=stage_name)
        return prompt

    @staticmethod
    def _requested_options(job: CsvJob) -> tuple[list[str], list[str], list[str]]:
        snapshot = Repository.json_field_dict(job.config_snapshot_json)
        genders = [str(value or "").strip().lower() for value in snapshot.get("person_gender_options", []) if str(value or "").strip()]
        ages = [str(value or "").strip().lower() for value in snapshot.get("person_age_options", []) if str(value or "").strip()]
        skins = [str(value or "").strip().lower() for value in snapshot.get("person_skin_color_options", []) if str(value or "").strip()]
        return genders, ages, skins

    def _expected_slot_names(self, job: CsvJob) -> list[str]:
        genders, ages, skins = self._requested_options(job)
        return [
            inventory_slot_column_name(age, gender, skin_color, background)
            for age in ages
            for gender in genders
            for skin_color in skins
            for background in BACKGROUND_VALUES
        ]

    def latest_entry_inventory_row(self, entry: Entry, *, source_row_id: str = "") -> dict[str, object] | None:
        if inventory_engine is None:
            return None
        with inventory_engine.begin() as conn:
            query = select(word_inventory)
            if source_row_id:
                query = query.where(word_inventory.c.id == source_row_id)
            else:
                query = query.where(word_inventory.c.source_entry_id == entry.id)
            row = conn.execute(
                query.order_by(desc(word_inventory.c.updated_at), desc(word_inventory.c.created_at)).limit(1)
            ).mappings().first()
        return dict(row) if row else None

    def slot_path_for_entry_profile(
        self,
        entry: Entry,
        profile: dict[str, str],
        *,
        background: str,
        source_row_id: str = "",
    ) -> str:
        row = self.latest_entry_inventory_row(entry, source_row_id=source_row_id)
        if not row:
            return ""
        slot_name = inventory_slot_column_name(
            str(profile.get("age") or "").strip().lower(),
            str(profile.get("gender") or "").strip().lower(),
            str(profile.get("skin_color") or "").strip().lower(),
            background,
        )
        return str(row.get(slot_name) or "").strip()

    def available_profiles_for_entry(self, entry: Entry) -> list[dict[str, object]]:
        row = self.latest_entry_inventory_row(entry)
        if not row:
            return []
        profiles: list[dict[str, object]] = []
        for age in AGE_VALUES:
            for gender in GENDER_VALUES:
                for skin_color in SKIN_VALUES:
                    regular_path = str(row.get(inventory_slot_column_name(age, gender, skin_color, "regular")) or "").strip()
                    white_bg_path = str(row.get(inventory_slot_column_name(age, gender, skin_color, "white_bg")) or "").strip()
                    if not regular_path and not white_bg_path:
                        continue
                    regular_asset = self.repo.get_asset_by_abs_path(regular_path) if regular_path else None
                    white_bg_asset = self.repo.get_asset_by_abs_path(white_bg_path) if white_bg_path else None
                    profiles.append(
                        {
                            "profile_key": f"{gender}:{age}:{skin_color}",
                            "regular_asset_id": regular_asset.id if regular_asset is not None else None,
                            "white_bg_asset_id": white_bg_asset.id if white_bg_asset is not None else None,
                            "regular_path": regular_path,
                            "white_bg_path": white_bg_path,
                        }
                    )
        return profiles

    def available_profiles_for_entries(self, entries: list[Entry]) -> dict[str, list[dict[str, object]]]:
        if inventory_engine is None:
            return {}
        normalized_entries = [entry for entry in entries if entry is not None]
        if not normalized_entries:
            return {}
        entry_ids = [entry.id for entry in normalized_entries if str(entry.id or "").strip()]
        if not entry_ids:
            return {}

        with inventory_engine.begin() as conn:
            rows = list(
                conn.execute(
                    select(word_inventory)
                    .where(word_inventory.c.source_entry_id.in_(entry_ids))
                    .order_by(
                        word_inventory.c.source_entry_id.asc(),
                        desc(word_inventory.c.updated_at),
                        desc(word_inventory.c.created_at),
                    )
                ).mappings()
            )

        latest_by_entry: dict[str, dict[str, object]] = {}
        for row in rows:
            entry_id = str(row.get("source_entry_id") or "").strip()
            if entry_id and entry_id not in latest_by_entry:
                latest_by_entry[entry_id] = dict(row)

        all_paths: list[str] = []
        for row in latest_by_entry.values():
            for age in AGE_VALUES:
                for gender in GENDER_VALUES:
                    for skin_color in SKIN_VALUES:
                        regular_path = str(row.get(inventory_slot_column_name(age, gender, skin_color, "regular")) or "").strip()
                        white_bg_path = str(row.get(inventory_slot_column_name(age, gender, skin_color, "white_bg")) or "").strip()
                        if regular_path:
                            all_paths.append(regular_path)
                        if white_bg_path:
                            all_paths.append(white_bg_path)

        assets_by_path = self.repo.get_assets_by_abs_paths(all_paths)

        result: dict[str, list[dict[str, object]]] = {}
        for entry_id, row in latest_by_entry.items():
            profiles: list[dict[str, object]] = []
            for age in AGE_VALUES:
                for gender in GENDER_VALUES:
                    for skin_color in SKIN_VALUES:
                        regular_path = str(row.get(inventory_slot_column_name(age, gender, skin_color, "regular")) or "").strip()
                        white_bg_path = str(row.get(inventory_slot_column_name(age, gender, skin_color, "white_bg")) or "").strip()
                        if not regular_path and not white_bg_path:
                            continue
                        regular_asset = assets_by_path.get(regular_path)
                        white_bg_asset = assets_by_path.get(white_bg_path)
                        profiles.append(
                            {
                                "profile_key": f"{gender}:{age}:{skin_color}",
                                "regular_asset_id": regular_asset.id if regular_asset is not None else None,
                                "white_bg_asset_id": white_bg_asset.id if white_bg_asset is not None else None,
                                "regular_path": regular_path,
                                "white_bg_path": white_bg_path,
                            }
                        )
            result[entry_id] = profiles
        return result

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
        prompt_values = {
            column.name: ""
            for column in word_inventory.columns
            if column.name.endswith("_prompt")
        }
        failures: list[dict[str, str]] = []
        source_row = Repository.json_field_dict(item.source_row_json)
        source_row_id = str(source_row.get("_word_source_row_id") or "").strip()
        existing_row = self.latest_entry_inventory_row(entry, source_row_id=source_row_id) or {}

        if item.base_regular_asset_id:
            asset = self.repo.get_asset(item.base_regular_asset_id)
            if asset is not None:
                slot_values[inventory_slot_column_name("kid", "male", "white", "regular")] = asset.abs_path
                prompt = self._prompt_for_stage(run_id=asset.run_id, stage_name="stage3_upgrade", attempt=int(asset.attempt or 0))
                if prompt is not None:
                    prompt_values[inventory_prompt_column_name("kid", "male", "white", "regular")] = prompt.prompt_text
        if item.base_white_bg_asset_id:
            asset = self.repo.get_asset(item.base_white_bg_asset_id)
            if asset is not None:
                slot_values[inventory_slot_column_name("kid", "male", "white", "white_bg")] = asset.abs_path
                prompt = self._prompt_for_stage(run_id=asset.run_id, stage_name="stage4_background", attempt=int(asset.attempt or 0))
                if prompt is not None:
                    prompt_values[inventory_prompt_column_name("kid", "male", "white", "white_bg")] = prompt.prompt_text

        for task in tasks:
            profile = str(task.profile_key or "").split(":")
            if len(profile) == 3:
                gender, age, skin_color = profile[0], profile[1], profile[2]
                if task.regular_asset_id:
                    regular = self.repo.get_asset(task.regular_asset_id)
                    if regular is not None:
                        slot_values[inventory_slot_column_name(age, gender, skin_color, "regular")] = regular.abs_path
                        prompt = self._prompt_for_stage(
                            run_id=regular.run_id,
                            stage_name="stage4_variant_generate",
                            attempt=int(regular.attempt or 0),
                        )
                        if prompt is not None:
                            prompt_values[inventory_prompt_column_name(age, gender, skin_color, "regular")] = prompt.prompt_text
                if task.white_bg_asset_id:
                    white_bg = self.repo.get_asset(task.white_bg_asset_id)
                    if white_bg is not None:
                        slot_values[inventory_slot_column_name(age, gender, skin_color, "white_bg")] = white_bg.abs_path
                        prompt = self._prompt_for_stage(
                            run_id=white_bg.run_id,
                            stage_name="stage5_variant_white_bg",
                            attempt=int(white_bg.attempt or 0),
                        )
                        if prompt is not None:
                            prompt_values[inventory_prompt_column_name(age, gender, skin_color, "white_bg")] = prompt.prompt_text
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

        # Keep previously created inventory slots/prompts unless the current sync has a newer value
        # for that exact slot. This lets Submit/CSV planning reuse older variants correctly.
        for key, value in list(slot_values.items()):
            if str(value or "").strip():
                continue
            prior_value = str(existing_row.get(key) or "").strip()
            if prior_value:
                slot_values[key] = prior_value
        for key, value in list(prompt_values.items()):
            if str(value or "").strip():
                continue
            prior_value = str(existing_row.get(key) or "").strip()
            if prior_value:
                prompt_values[key] = prior_value

        expected_slots = self._expected_slot_names(job)
        missing_slots = [slot for slot in expected_slots if not str(slot_values.get(slot) or "").strip()]
        now = datetime.utcnow()
        has_person_value = str(getattr(entry, "has_person", "") or "").strip().lower()
        shadow_run = self.repo.get_run(item.shadow_run_id) if item.shadow_run_id else None
        if has_person_value not in {"yes", "no"} and item.shadow_run_id:
            winner_attempt = 0
            if item.base_regular_asset_id:
                regular_asset = self.repo.get_asset(item.base_regular_asset_id)
                winner_attempt = int(regular_asset.attempt or 0) if regular_asset is not None else 0
            prompt = None
            if winner_attempt > 0:
                prompt = self._prompt_for_stage(
                    run_id=item.shadow_run_id,
                    stage_name="stage3_upgrade",
                    attempt=winner_attempt,
                )
            if prompt is None:
                prompt = self._prompt_for_stage(run_id=item.shadow_run_id, stage_name="stage1_prompt", attempt=1)
            if prompt is not None:
                prompt_need = str(prompt.needs_person or "").strip().lower()
                if prompt_need in {"yes", "no"}:
                    has_person_value = prompt_need
        if has_person_value not in {"yes", "no"}:
            prior_value = str(existing_row.get("has_person") or "").strip().lower()
            if prior_value in {"yes", "no"}:
                has_person_value = prior_value
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
            "has_person": has_person_value,
            "image_score": float(shadow_run.quality_score) if shadow_run and shadow_run.quality_score is not None else existing_row.get("image_score"),
            "needs_person_attention": bool(
                shadow_run
                and shadow_run.quality_score is not None
                and shadow_run.quality_threshold is not None
                and float(shadow_run.quality_score) < float(shadow_run.quality_threshold)
            ) if shadow_run is not None else bool(existing_row.get("needs_person_attention") or False),
            "job_status": item.status,
            "fully_complete": item.status == "completed" and not missing_slots and not failures,
            "missing_slots_json": json.dumps(missing_slots, ensure_ascii=True),
            "failure_reasons_json": json.dumps(failures, ensure_ascii=True),
            "synced_at": now,
            "updated_at": now,
            **slot_values,
            **prompt_values,
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
                if name.endswith("_path") or name.endswith("_prompt"):
                    export_row[name] = payload.get(name, "")
            rows.append(export_row)
        return rows

    def _sync_single_item(
        self,
        *,
        conn,
        job: CsvJob,
        item: CsvJobItem,
        tasks: list[CsvTaskNode],
    ) -> int:
        entry = self.repo.get_entry(item.entry_id)
        if entry is None:
            return 0
        payload = self._row_payload(job=job, item=item, entry=entry, tasks=tasks)
        source_row = Repository.json_field_dict(item.source_row_json)
        source_table = str(source_row.get("_word_source_table") or "").strip().lower()
        source_row_id = str(source_row.get("_word_source_row_id") or "").strip()
        source_word = str(source_row.get("_word_source_word") or "").strip()
        source_pos = str(source_row.get("_word_source_part_of_speech") or "").strip()
        source_sense_id = str(source_row.get("_word_source_sense_id") or "").strip()
        if source_table == "word_inventory" and source_row_id:
            exact_key = [word_inventory.c.id == source_row_id]
            if source_word:
                exact_key.append(word_inventory.c.word == source_word)
            if source_pos:
                exact_key.append(word_inventory.c.part_of_speech == source_pos)
            if source_sense_id:
                exact_key.append(word_inventory.c.sense_id == source_sense_id)
            existing_query = select(word_inventory.c.id, word_inventory.c.created_at).where(and_(*exact_key))
        else:
            existing_query = (
                select(word_inventory.c.id, word_inventory.c.created_at)
                .where(word_inventory.c.source_entry_id == entry.id)
                .order_by(desc(word_inventory.c.updated_at), desc(word_inventory.c.created_at))
                .limit(1)
            )
        existing = conn.execute(existing_query).first()
        if source_table == "word_inventory" and source_row_id and existing is None:
            raise RuntimeError(
                "The selected word_inventory row no longer matches its word + POS + sense_id key"
            )
        if existing:
            payload["id"] = existing.id
            payload["created_at"] = existing.created_at
            conn.execute(
                update(word_inventory)
                .where(word_inventory.c.id == existing.id)
                .values(**payload)
            )
        else:
            payload["id"] = f"inv_{uuid4().hex[:24]}"
            payload["created_at"] = payload["updated_at"]
            conn.execute(word_inventory.insert().values(**payload))
        return 1

    def sync_csv_job_item(self, csv_job_id: str, csv_job_item_id: str) -> int:
        if inventory_engine is None:
            return 0
        job = self.repo.get_csv_job(csv_job_id)
        if job is None:
            return 0
        item = self.repo.get_csv_job_item(csv_job_item_id)
        if item is None or item.csv_job_id != csv_job_id:
            return 0
        tasks = [task for task in self.repo.list_csv_tasks(csv_job_id) if task.csv_job_item_id == csv_job_item_id]
        with inventory_engine.begin() as conn:
            return self._sync_single_item(conn=conn, job=job, item=item, tasks=tasks)

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
                synced += self._sync_single_item(
                    conn=conn,
                    job=job,
                    item=item,
                    tasks=tasks_by_item.get(item.id, []),
                )
        return synced
