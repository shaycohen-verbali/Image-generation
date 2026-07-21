from __future__ import annotations

import csv
import json
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import Asset, CsvJob, CsvJobItem, CsvTaskNode, Entry, Run
from app.inventory_models import inventory_slot_column_name
from app.schemas import ExecutionMode
from app.services.cost_estimator import summarize_run_costs
from app.services.csv_service import parse_entries_csv, validate_entry_row
from app.services.inventory_sync import InventorySyncService
from app.services.inventory_sync import normalize_csv_job_export_fields
from app.services.person_profiles import DEFAULT_AGE, DEFAULT_GENDER, DEFAULT_SKIN_COLOR, profile_key
from app.services.pipeline import PipelineRunner
from app.services.repository import Repository
from app.services.storage import exports_root, materialize_path, persist_csv_source, persist_export_artifact
from app.services.utils import sanitize_filename


def _generated_batch_id() -> str:
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    return f"csv_{stamp}_{uuid4().hex[:6]}"


IMPORT_COMMIT_CHUNK_SIZE = 25
ALLOWED_GENDER_OPTIONS = ("male", "female")
ALLOWED_AGE_OPTIONS = ("toddler", "kid", "tween", "teenager")
ALLOWED_SKIN_OPTIONS = ("white", "black", "asian", "brown")
TERMINAL_RUN_STATUSES = {"completed_pass", "completed_fail_threshold", "completed_base_assets", "failed_technical", "canceled"}
EXPORT_GENDER_ABBREVIATIONS = {"male": "m", "female": "f"}
EXPORT_AGE_ABBREVIATIONS = {"toddler": "td", "kid": "kd", "tween": "tw", "teenager": "tn"}
EXPORT_SKIN_ABBREVIATIONS = {"white": "w", "black": "b", "asian": "as", "brown": "br"}
EXPORT_BACKGROUND_ABBREVIATIONS = {"regular": "reg", "white_bg": "wbg"}
EXPORT_IMAGES_CSV_FIELDS = [
    "row_index",
    "word",
    "part_of_sentence",
    "category",
    "context",
    "gender",
    "age",
    "skin_color",
    "background_type",
    "variant_abbrev",
    "image_filename",
    "image_relative_path",
    "job_status",
    "fully_complete",
    "missing_slots_json",
    "failure_reasons_json",
]
EXPORT_PROMPTS_CSV_FIELDS = [
    "row_index",
    "word",
    "part_of_sentence",
    "category",
    "gender",
    "age",
    "skin_color",
    "background_type",
    "image_filename",
    "image_relative_path",
    "asset_id",
    "prompt_stage",
    "prompt_text",
    "source_storage_path",
]


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")


def _parse_profile_key(value: str) -> dict[str, str]:
    gender, age, skin_color = (str(value or "").split(":") + ["", "", ""])[:3]
    return {"gender": gender, "age": age, "skin_color": skin_color}


def _clean_requested_options(values: Any, allowed: tuple[str, ...]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in values if isinstance(values, list) else []:
        value = str(raw or "").strip().lower()
        if not value or value not in allowed or value in seen:
            continue
        cleaned.append(value)
        seen.add(value)
    return cleaned


def _row_task_key(item_id: str, step_name: str, profile: dict[str, str]) -> str:
    return f"{item_id}:{step_name}:{profile_key(profile)}"


def _dependency_profile_for(profile: dict[str, str]) -> dict[str, str] | None:
    """Return the profile whose regular image is the required source for generating this profile.

    Dependency table:
      white male kid          → None (base, no dependency)
      {non-white} male kid    → white male kid
      white female kid        → white male kid
      {non-white} female kid  → white female kid
      {any} male {non-kid}    → {same-race} male kid
      white female teenager   → white male teenager
      {any} female {non-kid}  → {same-race} female kid
    """
    gender = profile.get("gender", "")
    age = profile.get("age", "")
    skin = profile.get("skin_color", "")

    if gender == DEFAULT_GENDER and age == DEFAULT_AGE and skin == DEFAULT_SKIN_COLOR:
        return None  # base – no dependency

    if age == DEFAULT_AGE:
        if gender == DEFAULT_GENDER:
            return {"gender": DEFAULT_GENDER, "age": DEFAULT_AGE, "skin_color": DEFAULT_SKIN_COLOR}
        # female kid
        if skin == DEFAULT_SKIN_COLOR:
            return {"gender": DEFAULT_GENDER, "age": DEFAULT_AGE, "skin_color": DEFAULT_SKIN_COLOR}
        return {"gender": "female", "age": DEFAULT_AGE, "skin_color": DEFAULT_SKIN_COLOR}

    if gender == "female" and age == "teenager" and skin == DEFAULT_SKIN_COLOR:
        return {"gender": DEFAULT_GENDER, "age": "teenager", "skin_color": DEFAULT_SKIN_COLOR}

    # non-kid: depends on same-race same-gender kid
    return {"gender": gender, "age": DEFAULT_AGE, "skin_color": skin}


def _extract_google_image_safety_details(response_json: dict[str, Any]) -> dict[str, str]:
    if not isinstance(response_json, dict):
        return {}
    candidates = response_json.get("candidates")
    if not isinstance(candidates, list):
        return {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        finish_reason = str(candidate.get("finishReason") or "").strip()
        finish_message = str(candidate.get("finishMessage") or "").strip()
        if finish_reason == "IMAGE_SAFETY" or "filtered out because it violated Google's" in finish_message:
            return {
                "provider": "google",
                "finish_reason": finish_reason or "IMAGE_SAFETY",
                "finish_message": finish_message,
            }
    return {}


def _friendly_variant_error_summary(profile_key_value: str, error_text: str, response_json: dict[str, Any]) -> str:
    moderation = _extract_google_image_safety_details(response_json)
    if moderation:
        return f"Blocked by image safety policy for {profile_key_value.replace(':', '_')}"
    return error_text


def _branch_role_for(profile: dict[str, str]) -> str:
    gender = profile.get("gender", "")
    age = profile.get("age", "")
    skin = profile.get("skin_color", "")
    if gender == DEFAULT_GENDER and age == DEFAULT_AGE and skin == DEFAULT_SKIN_COLOR:
        return "base_profile"
    if age == DEFAULT_AGE and gender == DEFAULT_GENDER:
        return "race_male_kid"
    if age == DEFAULT_AGE and skin == DEFAULT_SKIN_COLOR:
        return "female_seed"
    if age == DEFAULT_AGE:
        return "race_female_kid"
    if skin == DEFAULT_SKIN_COLOR and gender == DEFAULT_GENDER:
        return "male_age_variant"
    if skin == DEFAULT_SKIN_COLOR:
        return "female_age_variant"
    if gender == DEFAULT_GENDER:
        return "race_male_age"
    return "race_female_age"


class CsvDagService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = Repository(db)

    def _runtime_snapshot(
        self,
        *,
        person_gender_options: list[str],
        person_age_options: list[str],
        person_skin_color_options: list[str],
        override_existing_variants: bool = False,
        continued_from_job_id: str = "",
    ) -> dict[str, Any]:
        config = self.repo.get_runtime_config()
        return {
            "quality_threshold": int(config.quality_threshold),
            "max_optimization_loops": int(config.max_optimization_loops),
            "image_aspect_ratio": str(config.image_aspect_ratio),
            "image_resolution": str(config.image_resolution),
            "image_format": str(config.image_format),
            "nano_banana_safety_level": str(getattr(config, "nano_banana_safety_level", "default")),
            "person_gender_options": list(person_gender_options),
            "person_age_options": list(person_age_options),
            "person_skin_color_options": list(person_skin_color_options),
            "override_existing_variants": bool(override_existing_variants),
            "continued_from_job_id": str(continued_from_job_id or "").strip(),
        }

    def _requested_profiles(self, job: CsvJob) -> list[dict[str, str]]:
        snapshot = self.repo.json_field_dict(job.config_snapshot_json)
        gender_options = _clean_requested_options(snapshot.get("person_gender_options", []), ALLOWED_GENDER_OPTIONS)
        age_options = _clean_requested_options(snapshot.get("person_age_options", []), ALLOWED_AGE_OPTIONS)
        skin_options = _clean_requested_options(snapshot.get("person_skin_color_options", []), ALLOWED_SKIN_OPTIONS)
        if not gender_options or not age_options or not skin_options:
            raise RuntimeError("CSV DAG jobs require at least one gender, age, and skin selection")
        return [
            {"gender": gender, "age": age, "skin_color": skin_color}
            for gender in gender_options
            for age in age_options
            for skin_color in skin_options
        ]

    def _override_existing_variants_enabled(self, job: CsvJob) -> bool:
        snapshot = self.repo.json_field_dict(job.config_snapshot_json)
        raw_value = snapshot.get("override_existing_variants", False)
        if isinstance(raw_value, bool):
            return raw_value
        return str(raw_value or "").strip().lower() in {"1", "true", "yes", "on"}

    def _continued_from_job_id(self, job: CsvJob) -> str:
        snapshot = self.repo.json_field_dict(job.config_snapshot_json)
        return str(snapshot.get("continued_from_job_id") or "").strip()

    def _requested_profile_keys(self, job: CsvJob) -> list[str]:
        try:
            return [profile_key(profile) for profile in self._requested_profiles(job)]
        except RuntimeError:
            return []

    def _requested_profile_history(self, job: CsvJob) -> list[dict[str, Any]]:
        history: list[dict[str, Any]] = []
        current: CsvJob | None = job
        visited: set[str] = set()
        while current is not None and current.id not in visited:
            visited.add(current.id)
            history.append(
                {
                    "job_id": current.id,
                    "batch_id": current.batch_id,
                    "requested_profiles": self._requested_profile_keys(current),
                    "created_at": current.created_at,
                    "status": current.status,
                    "is_current": current.id == job.id,
                }
            )
            parent_id = self._continued_from_job_id(current)
            current = self.repo.get_csv_job(parent_id) if parent_id else None
        history.reverse()
        return history

    def _source_row_payload(self, item: CsvJobItem, entry: Entry) -> dict[str, Any]:
        try:
            parsed = json.loads(item.source_row_json or "{}")
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        return {
            "word": entry.word,
            "part of speech": entry.part_of_sentence,
            "category": entry.category,
            "context": entry.context,
        }

    def _word_source_row_id(self, item: CsvJobItem) -> str:
        try:
            source_row = json.loads(item.source_row_json or "{}")
        except json.JSONDecodeError:
            source_row = {}
        if not isinstance(source_row, dict):
            return ""
        if str(source_row.get("_word_source_table") or "").strip().lower() != "word_inventory":
            return ""
        return str(source_row.get("_word_source_row_id") or "").strip()

    def _build_task_specs(
        self, item: CsvJobItem, entry: Entry, job: CsvJob
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Build task specs for each requested profile following the dependency table.

        Returns (created_specs, skipped_notes).  A profile is skipped (with a note) when its
        direct dependency is neither available in inventory nor scheduled as a task in this job.
        """
        inventory_service = InventorySyncService(self.db)
        source_row_id = self._word_source_row_id(item)
        source_row = self._source_row_payload(item, entry)
        source_existing_paths = source_row.get("_word_source_existing_paths")
        has_source_inventory_snapshot = isinstance(source_existing_paths, dict)
        source_existing_paths = source_existing_paths if has_source_inventory_snapshot else {}
        requested_profiles = self._requested_profiles(job)
        override = self._override_existing_variants_enabled(job)

        created_specs: list[dict[str, Any]] = []
        spec_by_task_key: dict[str, dict[str, Any]] = {}
        spec_by_profile_key: dict[str, dict[str, Any]] = {}
        skipped_notes: list[str] = []

        def inventory_regular_available(p: dict[str, str]) -> bool:
            if has_source_inventory_snapshot:
                slot_name = inventory_slot_column_name(p["age"], p["gender"], p["skin_color"], "regular")
                return bool(str(source_existing_paths.get(slot_name) or "").strip())
            return bool(
                inventory_service.slot_path_for_entry_profile(
                    entry,
                    p,
                    background="regular",
                    source_row_id=source_row_id,
                )
            )

        def inventory_any_available(p: dict[str, str]) -> bool:
            if has_source_inventory_snapshot:
                regular_slot = inventory_slot_column_name(p["age"], p["gender"], p["skin_color"], "regular")
                white_bg_slot = inventory_slot_column_name(p["age"], p["gender"], p["skin_color"], "white_bg")
                return bool(
                    str(source_existing_paths.get(regular_slot) or "").strip()
                    or str(source_existing_paths.get(white_bg_slot) or "").strip()
                )
            return bool(
                inventory_service.slot_path_for_entry_profile(
                    entry,
                    p,
                    background="regular",
                    source_row_id=source_row_id,
                )
                or inventory_service.slot_path_for_entry_profile(
                    entry,
                    p,
                    background="white_bg",
                    source_row_id=source_row_id,
                )
            )

        def _create_spec(p: dict[str, str], source_p: dict[str, str] | None, dep_task_key: str | None) -> str:
            step_name = "step1_base" if source_p is None else "step2_variant"
            tk = _row_task_key(item.id, step_name, p)
            dep_keys = [dep_task_key] if dep_task_key else []
            node = self.repo.create_csv_task_node_uncommitted(
                csv_job_id=item.csv_job_id,
                csv_job_item_id=item.id,
                step_name=step_name,
                task_key=tk,
                profile_key=profile_key(p),
                source_profile_key=profile_key(source_p) if source_p else "",
                branch_role=_branch_role_for(p),
                dependency_keys=dep_keys,
                dependency_task_ids=[],
                status="pending",
            )
            spec = {
                "step_name": step_name,
                "task_key": tk,
                "profile": p,
                "source_profile": source_p or {},
                "branch_role": _branch_role_for(p),
                "dependency_keys": dep_keys,
                "node": node,
            }
            spec_by_task_key[tk] = spec
            spec_by_profile_key[profile_key(p)] = spec
            created_specs.append(spec)
            return tk

        def _ensure_profile_ready(p: dict[str, str], *, requested: bool) -> str | None:
            pk = profile_key(p)
            existing_spec = spec_by_profile_key.get(pk)
            if existing_spec is not None:
                return existing_spec["task_key"]

            # Never replace either image in an existing requested profile unless the user opted
            # into override. Intermediate dependencies may still reuse an existing regular asset.
            if requested and not override and inventory_any_available(p):
                return None
            if not requested and inventory_regular_available(p):
                return None

            dep = _dependency_profile_for(p)
            if dep is None:
                return _create_spec(p, None, None)

            dep_task_key: str | None = None
            if inventory_regular_available(dep):
                dep_task_key = None
            else:
                dep_task_key = _ensure_profile_ready(dep, requested=False)

            if dep_task_key is None and not inventory_regular_available(dep):
                skipped_notes.append(
                    f"Skipped {pk}: dependency profile '{profile_key(dep)}' is not available"
                )
                return None

            return _create_spec(p, dep, dep_task_key)

        for prof in requested_profiles:
            _ensure_profile_ready(prof, requested=True)

        # Wire dependency task IDs into each node's JSON field
        self.db.flush()
        for spec in created_specs:
            dep_ids = [
                spec_by_task_key[k]["node"].id
                for k in spec["dependency_keys"]
                if k in spec_by_task_key
            ]
            if dep_ids:
                spec["node"].dependency_task_ids_json = json.dumps(dep_ids, ensure_ascii=True)
                self.db.add(spec["node"])

        return created_specs, skipped_notes

    def import_csv_job(
        self,
        *,
        file_name: str,
        content: bytes,
        execution_mode: ExecutionMode,
        person_gender_options: list[str],
        person_age_options: list[str],
        person_skin_color_options: list[str],
        override_existing_variants: bool = False,
    ) -> dict[str, Any]:
        if execution_mode != "csv_dag":
            raise RuntimeError("CsvDagService only supports csv_dag execution mode")

        rows = parse_entries_csv(content)
        batch_id = _generated_batch_id()
        snapshot = self._runtime_snapshot(
            person_gender_options=person_gender_options,
            person_age_options=person_age_options,
            person_skin_color_options=person_skin_color_options,
            override_existing_variants=override_existing_variants,
            continued_from_job_id="",
        )
        job = self.repo.create_csv_job(
            batch_id=batch_id,
            source_file_name=file_name,
            execution_mode=execution_mode,
            config_snapshot={**snapshot, "source_csv_path": persist_csv_source(batch_id or "csv_job", file_name, content).persisted_path},
        )

        results: list[dict[str, Any]] = []
        imported_count = 0
        skipped_count = 0
        pending_rows_in_chunk = 0
        try:
            for index, row in enumerate(rows, start=1):
                error = validate_entry_row(row)
                if error:
                    skipped_count += 1
                    results.append({"row_index": index, "status": "invalid", "error": error})
                    continue
                payload = {
                    **row,
                    "batch": batch_id,
                    "person_gender_options": person_gender_options,
                    "person_age_options": person_age_options,
                    "person_skin_color_options": person_skin_color_options,
                }
                entry = self.repo.create_entry_uncommitted(payload)
                item = self.repo.create_csv_job_item_uncommitted(
                    csv_job_id=job.id,
                    entry_id=entry.id,
                    row_index=index,
                    source_row=row,
                )
                created_specs, skipped_notes = self._build_task_specs(item, entry, job)
                if not created_specs:
                    item.status = "completed"
                    item.error_detail = (
                        "; ".join(skipped_notes[:3]) if skipped_notes
                        else "Requested variants already exist in inventory"
                    )
                    self.db.add(item)
                elif skipped_notes:
                    item.error_detail = "Partial skip: " + "; ".join(skipped_notes[:3])
                    self.db.add(item)
                imported_count += 1
                pending_rows_in_chunk += 1
                row_result: dict[str, Any] = {"row_index": index, "status": "imported", "entry_id": entry.id}
                if skipped_notes:
                    row_result["skipped_profiles"] = skipped_notes
                results.append(row_result)

                if pending_rows_in_chunk >= IMPORT_COMMIT_CHUNK_SIZE:
                    self.db.commit()
                    pending_rows_in_chunk = 0
            if pending_rows_in_chunk:
                self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        if imported_count == 0:
            self.repo.update_csv_job(job, status="failed", error_detail="No valid CSV rows were imported", finished_at=datetime.utcnow())
        else:
            # Finalize ensures "imported" status even if the race window already set it to "completed"
            self.repo.finalize_csv_job_status(job.id)
        return {
            "job_id": job.id,
            "batch_id": batch_id,
            "status": self.repo.get_csv_job(job.id).status if self.repo.get_csv_job(job.id) else job.status,
            "imported_count": imported_count,
            "skipped_count": skipped_count,
            "execution_mode": execution_mode,
            "rows": results,
            "continued_from_job_id": None,
        }

    def import_word_source_rows(
        self,
        *,
        table_name: str,
        rows: list[dict[str, Any]],
        person_gender_options: list[str],
        person_age_options: list[str],
        person_skin_color_options: list[str],
        override_existing_variants: bool = False,
    ) -> dict[str, Any]:
        batch_id = _generated_batch_id()
        snapshot = self._runtime_snapshot(
            person_gender_options=person_gender_options,
            person_age_options=person_age_options,
            person_skin_color_options=person_skin_color_options,
            override_existing_variants=override_existing_variants,
            continued_from_job_id="",
        )
        job = self.repo.create_csv_job(
            batch_id=batch_id,
            source_file_name=f"supabase:{table_name}",
            execution_mode="csv_dag",
            config_snapshot={
                **snapshot,
                "word_source_type": "supabase_table",
                "word_source_table": table_name,
            },
        )

        results: list[dict[str, Any]] = []
        imported_count = 0
        skipped_count = 0
        pending_rows_in_chunk = 0
        try:
            for index, row in enumerate(rows, start=1):
                error = validate_entry_row(row)
                if error:
                    skipped_count += 1
                    results.append({"row_index": index, "status": "invalid", "error": error})
                    continue
                payload = {
                    **row,
                    "batch": batch_id,
                    "person_gender_options": person_gender_options,
                    "person_age_options": person_age_options,
                    "person_skin_color_options": person_skin_color_options,
                }
                entry = self.repo.create_entry_uncommitted(payload)
                item = self.repo.create_csv_job_item_uncommitted(
                    csv_job_id=job.id,
                    entry_id=entry.id,
                    row_index=index,
                    source_row=row,
                )
                created_specs, skipped_notes = self._build_task_specs(item, entry, job)
                if not created_specs:
                    item.status = "completed"
                    item.error_detail = (
                        "; ".join(skipped_notes[:3]) if skipped_notes
                        else "Requested variants already exist in inventory"
                    )
                    self.db.add(item)
                elif skipped_notes:
                    item.error_detail = "Partial skip: " + "; ".join(skipped_notes[:3])
                    self.db.add(item)
                imported_count += 1
                pending_rows_in_chunk += 1
                row_result: dict[str, Any] = {
                    "row_index": index,
                    "status": "imported",
                    "entry_id": entry.id,
                }
                if skipped_notes:
                    row_result["skipped_profiles"] = skipped_notes
                if len(results) < 500:
                    results.append(row_result)
                if pending_rows_in_chunk >= IMPORT_COMMIT_CHUNK_SIZE:
                    self.db.commit()
                    pending_rows_in_chunk = 0
            if pending_rows_in_chunk:
                self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        if imported_count == 0:
            self.repo.update_csv_job(
                job,
                status="failed",
                error_detail="No valid word-source rows were imported",
                finished_at=datetime.utcnow(),
            )
        else:
            self.repo.finalize_csv_job_status(job.id)
        refreshed = self.repo.get_csv_job(job.id)
        return {
            "job_id": job.id,
            "batch_id": batch_id,
            "status": refreshed.status if refreshed else job.status,
            "imported_count": imported_count,
            "skipped_count": skipped_count,
            "execution_mode": "csv_dag",
            "rows": results,
            "continued_from_job_id": None,
        }

    def continue_job(
        self,
        job_id: str,
        *,
        person_gender_options: list[str],
        person_age_options: list[str],
        person_skin_color_options: list[str],
        override_existing_variants: bool = False,
    ) -> dict[str, Any]:
        source_job = self.repo.get_csv_job(job_id)
        if source_job is None:
            raise RuntimeError(f"CSV job not found: {job_id}")
        finalized = self.repo.finalize_csv_job_status(job_id) or source_job
        if finalized.status not in {"completed", "failed", "partial_failed", "canceled"}:
            raise RuntimeError("You can continue a CSV job only after it has finished")
        requested_profiles = [
            _clean_requested_options(person_gender_options, ALLOWED_GENDER_OPTIONS),
            _clean_requested_options(person_age_options, ALLOWED_AGE_OPTIONS),
            _clean_requested_options(person_skin_color_options, ALLOWED_SKIN_OPTIONS),
        ]
        if not all(requested_profiles):
            raise RuntimeError("Choose at least one gender, one age, and one skin color to continue")

        source_snapshot = self.repo.json_field_dict(source_job.config_snapshot_json)
        snapshot = self._runtime_snapshot(
            person_gender_options=requested_profiles[0],
            person_age_options=requested_profiles[1],
            person_skin_color_options=requested_profiles[2],
            override_existing_variants=override_existing_variants,
            continued_from_job_id=source_job.id,
        )
        if source_snapshot.get("source_csv_path"):
            snapshot["source_csv_path"] = source_snapshot["source_csv_path"]

        new_batch_id = _generated_batch_id()
        new_job = self.repo.create_csv_job(
            batch_id=new_batch_id,
            source_file_name=source_job.source_file_name,
            execution_mode="csv_dag",
            config_snapshot=snapshot,
        )

        source_items = self.repo.list_csv_job_items(source_job.id)
        imported_count = 0
        skipped_count = 0
        pending_rows_in_chunk = 0
        try:
            for source_item in source_items:
                entry = self.repo.get_entry(source_item.entry_id)
                if entry is None:
                    skipped_count += 1
                    continue
                source_row = self._source_row_payload(source_item, entry)
                # The compact path snapshot speeds up the initial Supabase range import,
                # but it predates images generated by that round. Continuations must read
                # current inventory state so they can reuse the newly completed images.
                source_row.pop("_word_source_existing_paths", None)
                item = self.repo.create_csv_job_item_uncommitted(
                    csv_job_id=new_job.id,
                    entry_id=entry.id,
                    row_index=source_item.row_index,
                    source_row=source_row,
                )
                created_specs, skipped_notes = self._build_task_specs(item, entry, new_job)
                if not created_specs:
                    item.status = "completed"
                    item.error_detail = (
                        "; ".join(skipped_notes[:3]) if skipped_notes
                        else "Requested variants already exist in inventory"
                    )
                    self.db.add(item)
                elif skipped_notes:
                    item.error_detail = "Partial skip: " + "; ".join(skipped_notes[:3])
                    self.db.add(item)
                imported_count += 1
                pending_rows_in_chunk += 1
                if pending_rows_in_chunk >= IMPORT_COMMIT_CHUNK_SIZE:
                    self.db.commit()
                    pending_rows_in_chunk = 0
            if pending_rows_in_chunk:
                self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        if imported_count == 0:
            failed_job = self.repo.update_csv_job(
                new_job,
                status="failed",
                error_detail="No rows were available to continue",
                finished_at=datetime.utcnow(),
            )
            return {
                "job_id": failed_job.id,
                "batch_id": failed_job.batch_id,
                "status": failed_job.status,
                "imported_count": imported_count,
                "skipped_count": skipped_count,
                "continued_from_job_id": source_job.id,
            }

        started_job = self.start_job(new_job.id)
        return {
            "job_id": started_job.id,
            "batch_id": started_job.batch_id,
            "status": started_job.status,
            "imported_count": imported_count,
            "skipped_count": skipped_count,
            "continued_from_job_id": source_job.id,
        }

    def list_jobs(self) -> list[dict[str, Any]]:
        jobs = self.repo.list_csv_jobs()
        row_counts = self.repo.get_csv_job_row_counts([job.id for job in jobs])
        output: list[dict[str, Any]] = []
        for job in jobs:
            duration_seconds = 0.0
            if job.started_at:
                duration_end = job.finished_at or datetime.utcnow()
                duration_seconds = max(0.0, (duration_end - job.started_at).total_seconds())
            output.append(
                self._serialize_job(
                    job,
                    {
                        "total_row_count": row_counts.get(job.id, 0),
                        "duration_seconds": duration_seconds,
                    },
                )
            )
        return output

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        overview = self.repo.csv_job_overview(job_id)
        if overview is None:
            return None
        return self._serialize_job(overview["job"], overview)

    def clear_terminal_jobs(self) -> dict[str, Any]:
        deleted = self.repo.delete_csv_jobs(terminal_only=True)
        return {"deleted_job_count": deleted}

    def start_job(self, job_id: str) -> CsvJob:
        job = self.repo.get_csv_job(job_id)
        if job is None:
            raise RuntimeError(f"CSV job not found: {job_id}")
        tasks = self.repo.list_csv_tasks(job_id)
        if not tasks:
            finalized = self.repo.finalize_csv_job_status(job_id) or job
            return finalized
        self.repo.queue_pending_csv_tasks(job_id)
        started_at = job.started_at or datetime.utcnow()
        return self.repo.update_csv_job(job, status="queued", error_detail="", finished_at=None, started_at=started_at)

    def retry_failures(self, job_id: str) -> tuple[CsvJob, int]:
        count = self.repo.retry_failed_csv_tasks(job_id)
        job = self.repo.finalize_csv_job_status(job_id) or self.repo.get_csv_job(job_id)
        if job is None:
            raise RuntimeError(f"CSV job not found: {job_id}")
        return job, count

    def cancel_job(self, job_id: str) -> tuple[CsvJob, int]:
        canceled = self.repo.cancel_csv_job(job_id)
        job = self.repo.finalize_csv_job_status(job_id) or self.repo.get_csv_job(job_id)
        if job is None:
            raise RuntimeError(f"CSV job not found: {job_id}")
        return job, canceled

    def sync_inventory(self, job_id: str) -> dict[str, Any]:
        job = self.repo.get_csv_job(job_id)
        if job is None:
            raise RuntimeError(f"CSV job not found: {job_id}")
        self._backfill_has_person_for_job(job_id)
        service = InventorySyncService(self.db)
        synced = service.sync_csv_job(job_id)
        return {
            "job_id": job.id,
            "synced_row_count": synced,
            "inventory_enabled": service.enabled(),
        }

    def _ensure_shadow_run(self, item: CsvJobItem, job: CsvJob) -> Run:
        if item.shadow_run_id:
            existing = self.repo.get_run(item.shadow_run_id)
            if existing is not None:
                return existing
        config_snapshot = self.repo.json_field_dict(job.config_snapshot_json)
        shadow = self.repo.create_shadow_run(
            entry_id=item.entry_id,
            quality_threshold=int(config_snapshot.get("quality_threshold") or self.repo.get_runtime_config().quality_threshold),
            max_optimization_attempts=int(config_snapshot.get("max_optimization_loops") or self.repo.get_runtime_config().max_optimization_loops),
        )
        self.repo.update_csv_job_item(item, shadow_run_id=shadow.id)
        return shadow

    @staticmethod
    def _storage_prefix(job: CsvJob, item: CsvJobItem) -> str:
        return f"csv-jobs/{sanitize_filename(job.id)}/{sanitize_filename(item.id)}"

    def _read_has_person_from_shadow_run(self, shadow_run_id: str) -> str:
        """Extract has_person from the winning stage3_upgrade result of a shadow run."""
        run = self.repo.get_run(shadow_run_id)
        if run is None:
            return ""
        winner_attempt = max(1, int(run.optimization_attempt or 1))
        _, shadow_stages, _, _ = self.repo.run_snapshot(shadow_run_id)
        stage3 = next(
            (s for s in shadow_stages if s.stage_name == "stage3_upgrade" and int(s.attempt or 0) == winner_attempt),
            None,
        )
        if stage3 is None:
            return ""
        try:
            decision = json.loads(stage3.response_json or "{}").get("decision", {})
            raw = str(decision.get("resolved_need_person", "") or "").strip().lower()
            return "yes" if raw == "yes" else ("no" if raw == "no" else "")
        except Exception:
            return ""

    def _backfill_has_person_for_job(self, job_id: str) -> None:
        """For any item whose entry.has_person is empty, read it from the base shadow run and write it."""
        overview = self.repo.csv_job_overview(job_id)
        if overview is None:
            return
        for item in overview["items"]:
            if not item.shadow_run_id:
                continue
            entry = self.repo.get_entry(item.entry_id)
            if entry is None:
                continue
            if str(getattr(entry, "has_person", "") or "").strip():
                continue
            has_person_val = self._read_has_person_from_shadow_run(item.shadow_run_id)
            if has_person_val:
                self.repo.update_entry_has_person(entry.id, has_person_val)

    def _winner_base_assets(self, shadow_run_id: str) -> tuple[int, Asset | None, Asset | None, Asset | None]:
        shadow_run = self.repo.get_run(shadow_run_id)
        winner_attempt = max(1, int((shadow_run.optimization_attempt if shadow_run else 1) or 1))
        shadow_assets = self.repo.run_snapshot(shadow_run_id)[2]
        regular_asset = next(
            (
                asset for asset in shadow_assets
                if asset.stage_name == "stage3_upgraded" and int(asset.attempt or 0) == winner_attempt
            ),
            None,
        )
        white_bg_asset = next(
            (
                asset for asset in shadow_assets
                if asset.stage_name == "stage4_white_bg" and int(asset.attempt or 0) == winner_attempt
            ),
            None,
        )
        soften_asset = next(
            (
                asset
                for asset in shadow_assets
                if asset.stage_name == "stage3_post_quality_accessibility_generate" and int(asset.attempt or 0) == winner_attempt
            ),
            None,
        )
        return winner_attempt, regular_asset, white_bg_asset, soften_asset

    @staticmethod
    def _loop_count_from_snapshot(snapshot: dict[str, Any] | None, fallback_attempt: int | None) -> int | None:
        data = snapshot or {}
        scores = data.get("scores") if isinstance(data.get("scores"), list) else []
        stages = data.get("stages") if isinstance(data.get("stages"), list) else []
        attempts: list[int] = []
        for score in scores:
            attempt = int(getattr(score, "attempt", 0) or 0)
            if attempt > 0:
                attempts.append(attempt)
        for stage in stages:
            stage_name = str(getattr(stage, "stage_name", "") or "")
            if stage_name not in {"stage3_upgrade", "quality_gate"}:
                continue
            attempt = int(getattr(stage, "attempt", 0) or 0)
            if attempt > 0:
                attempts.append(attempt)
        if attempts:
            return max(attempts)
        fallback = int(fallback_attempt or 0)
        return fallback or None

    @staticmethod
    def _step_label(step_name: str) -> str:
        return {
            "step1_base": "Base images",
            "step2_variant": "Variant image",
            # legacy names kept for backward compatibility with existing task rows
            "step2_male_age": "Male age variant",
            "step3_female_white": "Female white variant",
            "step4_race_variant": "Race variant",
        }.get(str(step_name or ""), str(step_name or "Unknown step"))

    def _item_progress_payload(
        self,
        item: CsvJobItem,
        tasks: list[CsvTaskNode],
        *,
        previously_done: bool = False,
    ) -> dict[str, Any]:
        relevant = [task for task in tasks if task.csv_job_item_id == item.id]
        task_by_id = {task.id: task for task in relevant}
        requested_profile_keys = [
            key for key in dict.fromkeys(str(task.profile_key or "").strip() for task in relevant) if key
        ]
        item_status = str(item.status or "").lower()
        counts = {"pending": 0, "queued": 0, "running": 0, "completed": 0, "failed": 0, "canceled": 0}
        for task in relevant:
            status = str(task.status or "").lower()
            if status in counts:
                counts[status] += 1
        total = len(relevant)
        running_task = next((task for task in relevant if task.status == "running"), None)
        waiting_task = next((task for task in relevant if task.status in {"queued", "pending"}), None)
        failed_task = next((task for task in relevant if task.status == "failed"), None)
        completed_task = next((task for task in reversed(relevant) if task.status == "completed"), None)
        all_canceled = total > 0 and all(task.status == "canceled" for task in relevant)
        blocking_reason = ""
        waiting_on_steps: list[str] = []

        if total == 0:
            if item_status == "completed":
                return {
                    "main_status": "previously_done" if previously_done else "completed",
                    "sub_status": (
                        "Completed in a previous round; no new images were needed"
                        if previously_done
                        else str(item.error_detail or "No new images were needed")
                    ),
                    "current_step": "",
                    "current_profile_key": "",
                    "requested_profile_keys": requested_profile_keys,
                    "blocking_reason": "",
                    "waiting_on_steps": [],
                    "progress": {
                        "completed": 0,
                        "total": 0,
                        "running": 0,
                        "waiting": 0,
                        "failed": 0,
                        "canceled": 0,
                    },
                }
            if item_status == "failed":
                return {
                    "main_status": "failure",
                    "sub_status": str(item.error_detail or "Task failed"),
                    "current_step": "",
                    "current_profile_key": "",
                    "requested_profile_keys": requested_profile_keys,
                    "blocking_reason": "",
                    "waiting_on_steps": [],
                    "progress": {
                        "completed": 0,
                        "total": 0,
                        "running": 0,
                        "waiting": 0,
                        "failed": 0,
                        "canceled": 0,
                    },
                }
            if item_status == "canceled":
                return {
                    "main_status": "failure",
                    "sub_status": "Canceled",
                    "current_step": "",
                    "current_profile_key": "",
                    "requested_profile_keys": requested_profile_keys,
                    "blocking_reason": "",
                    "waiting_on_steps": [],
                    "progress": {
                        "completed": 0,
                        "total": 0,
                        "running": 0,
                        "waiting": 0,
                        "failed": 0,
                        "canceled": 0,
                    },
                }
            if item_status in {"running", "queued"}:
                return {
                    "main_status": "running",
                    "sub_status": str(item.error_detail or "Preparing work"),
                    "current_step": "",
                    "current_profile_key": "",
                    "requested_profile_keys": requested_profile_keys,
                    "blocking_reason": "",
                    "waiting_on_steps": [],
                    "progress": {
                        "completed": 0,
                        "total": 0,
                        "running": 0,
                        "waiting": 0,
                        "failed": 0,
                        "canceled": 0,
                    },
                }

        if waiting_task is not None:
            try:
                dependency_ids = [str(value) for value in json.loads(waiting_task.dependency_task_ids_json or "[]") if str(value)]
            except json.JSONDecodeError:
                dependency_ids = []
            dependency_tasks = [task_by_id.get(task_id) for task_id in dependency_ids if task_by_id.get(task_id) is not None]
            waiting_on_steps = [
                self._step_label(dep.step_name)
                for dep in dependency_tasks
                if dep.status in {"queued", "pending", "running"}
            ]
            blocked_by_failed = next((dep for dep in dependency_tasks if dep.status == "failed"), None)
            blocked_by_canceled = next((dep for dep in dependency_tasks if dep.status == "canceled"), None)
            if blocked_by_failed is not None:
                blocking_reason = f"Blocked by failed {self._step_label(blocked_by_failed.step_name)}"
            elif blocked_by_canceled is not None:
                blocking_reason = f"Blocked by canceled {self._step_label(blocked_by_canceled.step_name)}"
            elif waiting_on_steps:
                blocking_reason = f"Waiting on {', '.join(waiting_on_steps[:2])}"

        main_status = "pending"
        sub_status = "Waiting to be picked up"
        current_step = self._step_label(waiting_task.step_name) if waiting_task is not None else ""

        if failed_task is not None or all_canceled:
            main_status = "failure"
            sub_status = "Canceled" if all_canceled else str(failed_task.error_summary or f"{self._step_label(failed_task.step_name)} failed")
            current_step = self._step_label(failed_task.step_name) if failed_task is not None else current_step
        elif total > 0 and counts["completed"] == total:
            main_status = "completed"
            sub_status = "All requested images are ready"
            current_step = ""
        elif running_task is not None:
            main_status = "running"
            sub_status = f"Creating {self._step_label(running_task.step_name)}"
            current_step = self._step_label(running_task.step_name)
        elif waiting_task is not None and str(waiting_task.status or "").lower() == "queued":
            main_status = "running"
            current_step = self._step_label(waiting_task.step_name)
            if blocking_reason:
                sub_status = blocking_reason
            else:
                sub_status = f"Queued for {self._step_label(waiting_task.step_name)}"
        elif counts["completed"] > 0 or item.shadow_run_id:
            main_status = "running"
            sub_status = (
                blocking_reason or f"Waiting for {self._step_label(waiting_task.step_name)}"
                if waiting_task is not None
                else "Preparing next step"
            )
            current_step = self._step_label(waiting_task.step_name) if waiting_task is not None else ""
        elif waiting_task is not None and blocking_reason:
            sub_status = blocking_reason

        if main_status == "pending" and previously_done:
            main_status = "previously_done"
            sub_status = "Completed in a previous round; new work is waiting"

        return {
            "main_status": main_status,
            "sub_status": sub_status,
            "current_step": current_step,
            "current_profile_key": (
                str((running_task or waiting_task or failed_task or completed_task).profile_key or "")
                if (running_task or waiting_task or failed_task or completed_task) is not None
                else ""
            ),
            "requested_profile_keys": requested_profile_keys,
            "blocking_reason": blocking_reason,
            "waiting_on_steps": waiting_on_steps,
            "progress": {
                "completed": counts["completed"],
                "total": total,
                "running": counts["running"],
                "waiting": counts["queued"] + counts["pending"],
                "failed": counts["failed"],
                "canceled": counts["canceled"],
            },
        }

    def _update_item_status(self, item: CsvJobItem) -> CsvJobItem:
        tasks = [task for task in self.repo.list_csv_tasks(item.csv_job_id) if task.csv_job_item_id == item.id]
        statuses = [task.status for task in tasks]
        if not statuses:
            return self.repo.update_csv_job_item(item, status="pending", error_detail="")
        if any(status == "running" for status in statuses):
            return self.repo.update_csv_job_item(item, status="running", error_detail="")
        if any(status == "failed" for status in statuses):
            first_failure = next((task for task in tasks if task.status == "failed"), None)
            return self.repo.update_csv_job_item(item, status="failed", error_detail=first_failure.error_summary if first_failure else "Task failed")
        if any(status == "queued" for status in statuses):
            next_status = "running" if item.shadow_run_id or any(status in {"completed", "canceled"} for status in statuses) else "queued"
            return self.repo.update_csv_job_item(item, status=next_status, error_detail="")
        if any(status == "pending" for status in statuses):
            next_status = "running" if item.shadow_run_id or any(status in {"completed", "canceled"} for status in statuses) else "pending"
            return self.repo.update_csv_job_item(item, status=next_status, error_detail="")
        if any(status == "canceled" for status in statuses):
            return self.repo.update_csv_job_item(item, status="canceled", error_detail="Canceled by user")
        return self.repo.update_csv_job_item(item, status="completed", error_detail="")

    def execute_task(self, task_id: str) -> CsvTaskNode:
        task = self.repo.get_csv_task(task_id)
        if task is None:
            raise RuntimeError(f"CSV task not found: {task_id}")
        job = self.repo.get_csv_job(task.csv_job_id)
        if job is None:
            raise RuntimeError(f"CSV job missing for task {task_id}")
        item = self.repo.get_csv_job_item(task.csv_job_item_id)
        if item is None:
            raise RuntimeError(f"CSV job item missing for task {task_id}")
        entry = self.repo.get_entry(item.entry_id)
        if entry is None:
            raise RuntimeError(f"Entry missing for CSV task {task_id}")

        if job.status in {"cancel_requested", "canceled"}:
            finished = self.repo.update_csv_task(
                task,
                status="canceled",
                error_summary="Canceled before execution",
                finished_at=datetime.utcnow(),
            )
            self._update_item_status(item)
            self.repo.finalize_csv_job_status(job.id)
            return finished

        snapshot = self.repo.json_field_dict(job.config_snapshot_json)
        attempt_number = int(task.attempt_count or 0) + 1
        self.repo.update_csv_task(task, attempt_count=attempt_number)
        runner = PipelineRunner(self.db)

        try:
            shadow_run = self._ensure_shadow_run(item, job)
            storage_prefix = self._storage_prefix(job, item)
            if task.step_name == "step1_base":
                completed_run = runner.process_base_run(shadow_run.id, storage_prefix=storage_prefix)
                completed_run = self.repo.get_run(shadow_run.id) or completed_run
                if completed_run.status != "completed_base_assets":
                    status_label = str(completed_run.status or "unknown")
                    detail = str(completed_run.error_detail or "").strip()
                    if detail:
                        raise RuntimeError(f"Base DAG run ended with status {status_label}: {detail}")
                    raise RuntimeError(f"Base DAG run ended with status {status_label}")
                winner_attempt, regular_asset, white_bg_asset, soften_asset = self._winner_base_assets(shadow_run.id)
                if regular_asset is None or white_bg_asset is None:
                    missing_assets: list[str] = []
                    if regular_asset is None:
                        missing_assets.append("regular")
                    if white_bg_asset is None:
                        missing_assets.append("white-background")
                    raise RuntimeError(
                        f"Base DAG winner attempt {winner_attempt} is missing {', '.join(missing_assets)} asset(s)"
                    )
                current_task = self.repo.get_csv_task(task.id)
                if current_task is None or current_task.status != "running":
                    return current_task or task
                self.repo.add_csv_task_attempt(
                    csv_task_node_id=task.id,
                    attempt_number=attempt_number,
                    status="completed",
                    request_json={"step_name": task.step_name, "shadow_run_id": shadow_run.id},
                    response_json={
                        "winner_attempt": winner_attempt,
                        "regular_asset_id": regular_asset.id,
                        "soften_asset_id": soften_asset.id if soften_asset else "",
                        "white_bg_asset_id": white_bg_asset.id,
                    },
                    finished_at=datetime.utcnow(),
                )
                finished_task = self.repo.update_csv_task(
                    task,
                    source_asset_id=regular_asset.id,
                    regular_asset_id=regular_asset.id,
                    white_bg_asset_id=white_bg_asset.id,
                    status="completed",
                    error_summary="",
                    finished_at=datetime.utcnow(),
                )
                self.repo.update_csv_job_item(
                    item,
                    base_regular_asset_id=regular_asset.id,
                    base_soften_asset_id=soften_asset.id if soften_asset else None,
                    base_white_bg_asset_id=white_bg_asset.id,
                )
                _, shadow_stages, _, _ = self.repo.run_snapshot(shadow_run.id)
                winning_stage3 = next(
                    (
                        s for s in shadow_stages
                        if s.stage_name == "stage3_upgrade" and int(s.attempt or 0) == winner_attempt
                    ),
                    None,
                )
                has_person = ""
                if winning_stage3:
                    try:
                        decision = json.loads(winning_stage3.response_json or "{}").get("decision", {})
                        raw = str(decision.get("resolved_need_person", "") or "").strip().lower()
                        has_person = "yes" if raw == "yes" else ("no" if raw == "no" else "")
                    except Exception:
                        pass
                if has_person:
                    self.repo.update_entry_has_person(entry.id, has_person)
            else:
                has_person_val = str(getattr(entry, "has_person", "") or "").strip().lower()
                if not has_person_val and item.shadow_run_id:
                    has_person_val = self._read_has_person_from_shadow_run(item.shadow_run_id)
                    if has_person_val:
                        self.repo.update_entry_has_person(entry.id, has_person_val)
                if has_person_val == "no":
                    self.repo.update_csv_task(
                        task,
                        status="completed",
                        error_summary="No person required for this word",
                        finished_at=datetime.utcnow(),
                    )
                    self.repo.add_csv_task_attempt(
                        csv_task_node_id=task.id,
                        attempt_number=attempt_number,
                        status="completed",
                        request_json={"step_name": task.step_name},
                        response_json={"skipped": True, "reason": "No person required for this word"},
                        finished_at=datetime.utcnow(),
                    )
                    return self.repo.get_csv_task(task.id) or task
                dependency_ids = [str(value) for value in json.loads(task.dependency_task_ids_json or "[]") if str(value)]
                target_profile = _parse_profile_key(task.profile_key)
                source_profile = _parse_profile_key(task.source_profile_key) if task.source_profile_key else None

                def _default_base_source_asset() -> Asset | None:
                    if not source_profile:
                        return None
                    if profile_key(source_profile) != f"{DEFAULT_GENDER}:{DEFAULT_AGE}:{DEFAULT_SKIN_COLOR}":
                        return None
                    if item.base_soften_asset_id:
                        softened = self.repo.get_asset(item.base_soften_asset_id)
                        if softened is not None:
                            return softened
                    if not item.base_regular_asset_id:
                        return None
                    return self.repo.get_asset(item.base_regular_asset_id)

                def _source_asset_for_dependency_task(source_task: CsvTaskNode | None) -> Asset | None:
                    if source_task is None:
                        return None
                    if source_task.step_name == "step1_base":
                        softened = _default_base_source_asset()
                        if softened is not None:
                            return softened
                    if source_task.regular_asset_id:
                        return self.repo.get_asset(source_task.regular_asset_id)
                    return None

                def _cannot_complete(reason: str) -> CsvTaskNode:
                    self.repo.update_csv_task(
                        task,
                        status="failed",
                        error_summary=reason,
                        finished_at=datetime.utcnow(),
                    )
                    self.repo.add_csv_task_attempt(
                        csv_task_node_id=task.id,
                        attempt_number=attempt_number,
                        status="failed",
                        request_json={"step_name": task.step_name},
                        response_json={"failed": True, "reason": reason},
                        error_detail=reason,
                        finished_at=datetime.utcnow(),
                    )
                    return self.repo.get_csv_task(task.id) or task

                if dependency_ids:
                    source_task = self.repo.get_csv_task(dependency_ids[0])
                    source_asset: Asset | str | None = _source_asset_for_dependency_task(source_task)
                    if source_asset is None:
                        source_asset = _default_base_source_asset()
                    if source_asset is None:
                        dep_label = profile_key(source_profile) if source_profile else dependency_ids[0]
                        return _cannot_complete(f"Cannot complete: dependency image for '{dep_label}' is not yet available")
                else:
                    if not source_profile:
                        raise RuntimeError(f"Task {task.task_key} has no dependency or reusable source profile")
                    inventory_source = InventorySyncService(self.db).slot_path_for_entry_profile(
                        entry,
                        source_profile,
                        background="regular",
                        source_row_id=self._word_source_row_id(item),
                    )
                    if not inventory_source:
                        return _cannot_complete(f"Cannot complete: dependency image for '{profile_key(source_profile)}' is not yet available")
                    source_asset = inventory_source
                winner_attempt = max(1, int((self.repo.get_run(shadow_run.id).optimization_attempt if self.repo.get_run(shadow_run.id) else 1) or 1))
                created = runner.create_profile_variant_pair(
                    owner_run_id=shadow_run.id,
                    entry=entry,
                    winner_attempt=winner_attempt,
                    profile=target_profile,
                    source_profile=source_profile,
                    source_asset=source_asset,
                    aspect_ratio=str(snapshot.get("image_aspect_ratio") or self.repo.get_runtime_config().image_aspect_ratio),
                    image_size=str(snapshot.get("image_resolution") or self.repo.get_runtime_config().image_resolution),
                    image_format=str(snapshot.get("image_format") or self.repo.get_runtime_config().image_format),
                    nano_banana_safety_level=str(snapshot.get("nano_banana_safety_level") or getattr(self.repo.get_runtime_config(), "nano_banana_safety_level", "default")),
                    storage_prefix=storage_prefix,
                )
                regular_asset = created["regular_asset"]
                white_bg_asset = created["white_bg_asset"]
                current_task = self.repo.get_csv_task(task.id)
                if current_task is None or current_task.status != "running":
                    return current_task or task
                self.repo.add_csv_task_attempt(
                    csv_task_node_id=task.id,
                    attempt_number=attempt_number,
                    status="completed",
                    request_json=created.get("request_json", {}),
                    response_json={
                        "regular_asset_id": regular_asset.id,
                        "white_bg_asset_id": white_bg_asset.id,
                        "prediction_id": created.get("prediction_id", ""),
                        "status_transitions": created.get("status_transitions", []),
                    },
                    finished_at=datetime.utcnow(),
                )
                finished_task = self.repo.update_csv_task(
                    task,
                    source_asset_id=source_asset.id if not isinstance(source_asset, str) else None,
                    regular_asset_id=regular_asset.id,
                    white_bg_asset_id=white_bg_asset.id,
                    status="completed",
                    error_summary="",
                    finished_at=datetime.utcnow(),
                )
        except Exception as exc:  # noqa: BLE001
            self.db.rollback()
            current_task = self.repo.get_csv_task(task.id)
            if current_task is None or current_task.status != "running":
                return current_task or task
            request_json = getattr(exc, "request_json", {}) if isinstance(getattr(exc, "request_json", {}), dict) else {}
            response_json = getattr(exc, "response_json", {}) if isinstance(getattr(exc, "response_json", {}), dict) else {}
            moderation = _extract_google_image_safety_details(response_json)
            error_summary = _friendly_variant_error_summary(task.profile_key, str(exc), response_json)
            attempt_response_json = dict(response_json)
            if moderation:
                attempt_response_json["moderation"] = moderation
            attempt_request_json = dict(request_json)
            attempt_request_json.setdefault("profile_key", task.profile_key)
            attempt_request_json.setdefault("source_profile_key", task.source_profile_key)
            self.repo.add_csv_task_attempt(
                csv_task_node_id=task.id,
                attempt_number=attempt_number,
                status="failed",
                request_json=attempt_request_json,
                response_json=attempt_response_json,
                error_detail=str(exc),
                finished_at=datetime.utcnow(),
            )
            finished_task = self.repo.update_csv_task(
                task,
                status="failed",
                error_summary=error_summary,
                finished_at=datetime.utcnow(),
            )
        finally:
            runner.google_images.close()

        self._update_item_status(item)
        finalized_job = self.repo.finalize_csv_job_status(job.id)
        self._backfill_has_person_for_job(job.id)
        InventorySyncService(self.db).sync_csv_job_item(job.id, item.id)
        return self.repo.get_csv_task(task.id) or finished_task

    def _serialize_job(self, job: CsvJob, overview: dict[str, Any]) -> dict[str, Any]:
        total_row_count = int(overview.get("total_row_count") or 0)
        duration_seconds = float(overview.get("duration_seconds") or 0)
        display_status = "running"
        display_sub_status = ""
        raw_status = str(job.status or "")
        if raw_status == "imported":
            display_status = "pending"
            display_sub_status = "Imported and not started yet"
        elif raw_status in {"queued", "retry_queued"}:
            display_status = "running"
            display_sub_status = "Queued under load"
        elif raw_status == "cancel_requested":
            display_status = "running"
            display_sub_status = "Stopping after active work finishes"
        elif raw_status == "completed":
            display_status = "completed"
            display_sub_status = "All rows finished"
        elif raw_status == "partial_failed":
            display_status = "failure"
            display_sub_status = "Some rows failed and some completed"
        elif raw_status == "failed":
            display_status = "failure"
            display_sub_status = "One or more rows failed"
        elif raw_status == "canceled":
            display_status = "failure"
            display_sub_status = "Canceled"
        else:
            display_status = "running"
            display_sub_status = "Work is in progress"
        return {
            "id": job.id,
            "batch_id": job.batch_id,
            "execution_mode": job.execution_mode,
            "source_file_name": job.source_file_name,
            "status": job.status,
            "display_status": display_status,
            "display_sub_status": display_sub_status,
            "error_detail": job.error_detail,
            "total_row_count": total_row_count,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "duration_seconds": duration_seconds,
            "requested_profiles": self._requested_profile_keys(job),
            "continued_from_job_id": self._continued_from_job_id(job) or None,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }

    @staticmethod
    def export_zip_name(batch_id: str) -> str:
        return f"{sanitize_filename(batch_id)}_export.zip"

    def export_local_zip_path(self, job: CsvJob) -> Path:
        export_dir = exports_root() / sanitize_filename(job.id)
        export_dir.mkdir(parents=True, exist_ok=True)
        return export_dir / self.export_zip_name(job.batch_id)

    @staticmethod
    def _selected_export_image_fields(selected_export_fields: list[str]) -> list[str]:
        return [field for field in selected_export_fields if field.endswith("_path")]

    @staticmethod
    def _parse_export_image_field(field_name: str) -> dict[str, str] | None:
        parts = str(field_name or "").split("_")
        if len(parts) < 5 or parts[-1] != "path":
            return None
        if parts[-3:-1] == ["white", "bg"]:
            background = "white_bg"
            core = parts[:-3]
        else:
            background = parts[-2]
            core = parts[:-2]
        if len(core) != 3:
            return None
        age, gender, skin_color = core
        if background not in EXPORT_BACKGROUND_ABBREVIATIONS:
            return None
        return {
            "age": age,
            "gender": gender,
            "skin_color": skin_color,
            "background_type": background,
        }

    @staticmethod
    def _export_variant_abbrev(*, gender: str, age: str, skin_color: str, background_type: str) -> str:
        return "_".join(
            [
                EXPORT_GENDER_ABBREVIATIONS.get(gender, sanitize_filename(gender or "unknown")),
                EXPORT_AGE_ABBREVIATIONS.get(age, sanitize_filename(age or "unknown")),
                EXPORT_SKIN_ABBREVIATIONS.get(skin_color, sanitize_filename(skin_color or "unknown")),
                EXPORT_BACKGROUND_ABBREVIATIONS.get(background_type, sanitize_filename(background_type or "unknown")),
            ]
        )

    @staticmethod
    def _export_image_filename(
        *,
        row_index: int,
        word: str,
        part_of_sentence: str,
        category: str,
        variant_abbrev: str,
        source_path: str,
    ) -> str:
        suffix = Path(str(source_path or "")).suffix.lower() or ".jpg"
        return "__".join(
            [
                f"{int(row_index or 0):04d}",
                sanitize_filename(word or "unknown-word"),
                sanitize_filename(part_of_sentence or "unknown-pos"),
                sanitize_filename(category or "no-category"),
                sanitize_filename(variant_abbrev or "variant"),
            ]
        ) + suffix

    @staticmethod
    def _export_image_relative_path(*, background_type: str, image_filename: str) -> str:
        background_dir = "white_background" if background_type == "white_bg" else "regular"
        return "/".join(
            [
                "images",
                background_dir,
                sanitize_filename(image_filename),
            ]
        )

    @staticmethod
    def _export_prompt_stage(background_type: str) -> str:
        return "stage5_variant_white_bg" if background_type == "white_bg" else "stage4_variant_generate"

    @staticmethod
    def _export_readme_text(batch_id: str) -> str:
        return f"""# Verbali CSV DAG Export

Batch: {batch_id}

## Primary files

- `images.csv` is the CTO-friendly image index. It has one row per exported image and includes the package-relative image path.
- `prompts.csv` is the prompt tracking index. It links each exported image filename/path back to its prompt text and source storage path.

## Image folders

Images are grouped by background type:

`images/regular/{{filename}}`

`images/white_background/{{filename}}`

Examples:

`images/regular/0001__fairly__adverb__no-category__f_tn_w_reg.jpg`

`images/white_background/0001__fairly__adverb__no-category__f_tn_w_wbg.jpg`

## Filename format

`{{row_index}}__{{word}}__{{part_of_sentence}}__{{category}}__{{variant_abbrev}}.jpg`

The row index is included to prevent collisions when the same word/POS/category appears more than once.

## Variant abbreviation legend

- Gender: `m` = male, `f` = female
- Age: `td` = toddler, `kd` = kid, `tw` = tween, `tn` = teenager
- Skin color: `w` = white, `b` = black, `as` = asian, `br` = brown
- Background: `reg` = regular, `wbg` = white background

## Metadata

Debugging and backwards-compatible files are under `_metadata/`.
"""

    def job_overview(self, job_id: str) -> dict[str, Any] | None:
        overview = self.repo.csv_job_overview(job_id)
        if overview is None:
            return None
        job = overview["job"]
        tasks = overview["tasks"]
        previously_completed_entry_ids: set[str] = set()
        parent_job_id = self._continued_from_job_id(job)
        visited_parent_ids: set[str] = set()
        while parent_job_id and parent_job_id not in visited_parent_ids:
            visited_parent_ids.add(parent_job_id)
            parent_job = self.repo.get_csv_job(parent_job_id)
            if parent_job is None:
                break
            previously_completed_entry_ids.update(
                str(parent_item.entry_id)
                for parent_item in self.repo.list_csv_job_items(parent_job.id)
                if str(parent_item.status or "").strip().lower() == "completed"
            )
            parent_job_id = self._continued_from_job_id(parent_job)
        inventory_service = InventorySyncService(self.db)
        entries_by_id = self.repo.get_entries_by_ids(
            [item.entry_id for item in overview["items"] if str(item.entry_id or "").strip()]
        )
        runs_by_id = self.repo.get_runs_by_ids(
            [item.shadow_run_id for item in overview["items"] if str(item.shadow_run_id or "").strip()]
        )
        terminal_shadow_run_ids = [
            run.id for run in runs_by_id.values() if str(run.status or "").strip().lower() in TERMINAL_RUN_STATUSES
        ]
        run_snapshots = self.repo.get_run_snapshots_by_ids(terminal_shadow_run_ids)
        cost_summary_by_run_id = {
            run_id: summarize_run_costs(snapshot.get("stages", []), snapshot.get("assets", []))
            for run_id, snapshot in run_snapshots.items()
        }
        available_profiles_by_entry = inventory_service.available_profiles_for_entries(list(entries_by_id.values()))
        items_payload: list[dict[str, Any]] = []
        word_counts = {"pending": 0, "running": 0, "completed": 0, "failure": 0, "previously_done": 0}
        total_estimated_cost_usd = 0.0
        provider_breakdown = {"google": 0.0, "replicate": 0.0, "openai": 0.0}
        for item in overview["items"]:
            entry = entries_by_id.get(item.entry_id)
            shadow_run = runs_by_id.get(item.shadow_run_id) if item.shadow_run_id else None
            item_progress = self._item_progress_payload(
                item,
                tasks,
                previously_done=str(item.entry_id) in previously_completed_entry_ids,
            )
            cost_summary = cost_summary_by_run_id.get(shadow_run.id, {}) if shadow_run else {}
            estimated_item_cost = (
                float(cost_summary.get("estimated_total_cost_usd") or 0.0)
                if (
                    shadow_run
                    and str(shadow_run.status or "").strip().lower() in TERMINAL_RUN_STATUSES
                    and item_progress["main_status"] == "completed"
                )
                else None
            )
            if estimated_item_cost is not None:
                total_estimated_cost_usd += estimated_item_cost
                for provider_name, provider_cost in (cost_summary.get("provider_breakdown") or {}).items():
                    if provider_name in provider_breakdown:
                        provider_breakdown[provider_name] += float(provider_cost or 0.0)
            available_profiles = available_profiles_by_entry.get(item.entry_id, []) if entry else []
            word_counts[item_progress["main_status"]] += 1
            items_payload.append(
                {
                    "id": item.id,
                    "entry_id": item.entry_id,
                    "row_index": item.row_index,
                    "word": entry.word if entry else "",
                    "part_of_sentence": entry.part_of_sentence if entry else "",
                    "category": entry.category if entry else "",
                    "status": item.status,
                    "error_detail": item.error_detail,
                    "shadow_run_id": item.shadow_run_id,
                    "shadow_run_status": shadow_run.status if shadow_run else "",
                    "shadow_run_current_stage": shadow_run.current_stage if shadow_run else "",
                    "shadow_run_error_detail": shadow_run.error_detail if shadow_run else "",
                    "optimization_attempt": shadow_run.optimization_attempt if shadow_run else None,
                    "optimization_loop_count": self._loop_count_from_snapshot(
                        run_snapshots.get(shadow_run.id) if shadow_run else None,
                        shadow_run.optimization_attempt if shadow_run else None,
                    ),
                    "quality_score": shadow_run.quality_score if shadow_run else None,
                    "quality_threshold": shadow_run.quality_threshold if shadow_run else None,
                    "needs_person_attention": bool(
                        shadow_run
                        and shadow_run.quality_score is not None
                        and shadow_run.quality_threshold is not None
                        and float(shadow_run.quality_score) < float(shadow_run.quality_threshold)
                    ),
                    "estimated_total_cost_usd": round(estimated_item_cost, 6) if estimated_item_cost is not None else None,
                    "provider_breakdown": {
                        key: round(float(value or 0.0), 6)
                        for key, value in (cost_summary.get("provider_breakdown") or {}).items()
                        if key in {"google", "replicate", "openai"}
                    } if estimated_item_cost is not None else {},
                    "base_regular_asset_id": item.base_regular_asset_id,
                    "base_soften_asset_id": item.base_soften_asset_id,
                    "base_white_bg_asset_id": item.base_white_bg_asset_id,
                    "main_status": item_progress["main_status"],
                    "sub_status": item_progress["sub_status"],
                    "current_step": item_progress["current_step"],
                    "current_profile_key": item_progress["current_profile_key"],
                    "requested_profile_keys": item_progress["requested_profile_keys"],
                    "available_profiles": available_profiles,
                    "blocking_reason": item_progress["blocking_reason"],
                    "waiting_on_steps": item_progress["waiting_on_steps"],
                    "progress": item_progress["progress"],
                    "has_person": str(getattr(entry, "has_person", "") or "") if entry else "",
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                }
            )
        tasks_payload = [
            {
                "id": task.id,
                "csv_job_item_id": task.csv_job_item_id,
                "step_name": task.step_name,
                "task_key": task.task_key,
                "profile_key": task.profile_key,
                "source_profile_key": task.source_profile_key,
                "branch_role": task.branch_role,
                "status": task.status,
                "attempt_count": task.attempt_count,
                "max_attempts": task.max_attempts,
                "error_summary": task.error_summary,
                "regular_asset_id": task.regular_asset_id,
                "white_bg_asset_id": task.white_bg_asset_id,
                "dependency_task_ids": [str(value) for value in json.loads(task.dependency_task_ids_json or "[]") if str(value)],
                "started_at": task.started_at,
                "finished_at": task.finished_at,
                "created_at": task.created_at,
                "updated_at": task.updated_at,
            }
            for task in overview["tasks"]
        ]
        export_dir = exports_root() / sanitize_filename(job.id)
        return {
            "job": self._serialize_job(job, overview),
            "step_counts": overview.get("step_counts", {}),
            "issues_by_step": overview.get("issues_by_step", {}),
            "items": items_payload,
            "tasks": tasks_payload,
            "word_counts": word_counts,
            "requested_profile_history": self._requested_profile_history(job),
            "estimated_total_cost_usd": round(total_estimated_cost_usd, 6) if total_estimated_cost_usd > 0 else None,
            "provider_breakdown": {
                key: round(value, 6) for key, value in provider_breakdown.items()
            },
            "export_ready": job.status in {"completed", "failed", "partial_failed", "canceled"},
            "export_id": job.id if self.export_local_zip_path(job).exists() else None,
        }

    def _reconcile_terminal_shadow_runs(self, job_id: str) -> bool:
        overview = self.repo.csv_job_overview(job_id)
        if overview is None:
            return False
        runs_by_id = self.repo.get_runs_by_ids(
            [item.shadow_run_id for item in overview["items"] if str(item.shadow_run_id or "").strip()]
        )
        changed = False
        now = datetime.utcnow()
        for item in overview["items"]:
            if not item.shadow_run_id:
                continue
            shadow_run = runs_by_id.get(item.shadow_run_id)
            if shadow_run is None:
                continue
            shadow_status = str(shadow_run.status or "")
            if shadow_status not in {"completed_base_assets", "failed_technical", "completed_fail_threshold", "canceled"}:
                continue
            active_tasks = [
                task for task in overview["tasks"]
                if task.csv_job_item_id == item.id and task.status in {"pending", "queued", "running"}
            ]
            if not active_tasks:
                continue
            if shadow_status == "completed_base_assets":
                winner_attempt, regular_asset, white_bg_asset, soften_asset = self._winner_base_assets(shadow_run.id)
                if regular_asset is None or white_bg_asset is None:
                    continue
                for task in active_tasks:
                    if task.step_name != "step1_base":
                        continue
                    self.repo.update_csv_task(
                        task,
                        source_asset_id=regular_asset.id,
                        regular_asset_id=regular_asset.id,
                        white_bg_asset_id=white_bg_asset.id,
                        status="completed",
                        error_summary="",
                        finished_at=now,
                    )
                self.repo.update_csv_job_item(
                    item,
                    error_detail="",
                    base_regular_asset_id=regular_asset.id,
                    base_soften_asset_id=soften_asset.id if soften_asset else None,
                    base_white_bg_asset_id=white_bg_asset.id,
                )
                entry = self.repo.get_entry(item.entry_id)
                if entry is not None:
                    has_person_val = self._read_has_person_from_shadow_run(item.shadow_run_id)
                    if has_person_val:
                        self.repo.update_entry_has_person(entry.id, has_person_val)
                self._update_item_status(item)
                changed = True
                continue
            if shadow_status == "canceled":
                task_status = "canceled"
                item_status = "canceled"
                summary = str(shadow_run.error_detail or "Shadow run was canceled")
            else:
                task_status = "failed"
                item_status = "failed"
                summary = f"Shadow run ended with status {shadow_status}"
                detail = str(shadow_run.error_detail or "").strip()
                if detail:
                    summary = f"{summary}: {detail}"
                elif shadow_run.current_stage:
                    summary = f"{summary} at {shadow_run.current_stage}"
            for task in active_tasks:
                self.repo.update_csv_task(
                    task,
                    status=task_status,
                    error_summary=summary,
                    finished_at=now,
                )
            self.repo.update_csv_job_item(
                item,
                status=item_status,
                error_detail=summary,
            )
            changed = True
        return changed

    def export_job(self, job_id: str, export_fields: list[str] | None = None) -> dict[str, Any]:
        inventory_service = InventorySyncService(self.db)
        export_warnings: list[str] = []
        try:
            inventory_service.sync_csv_job(job_id)
        except Exception as exc:  # noqa: BLE001
            export_warnings.append(f"Inventory sync skipped during export: {exc}")
        overview = self.repo.csv_job_overview(job_id)
        if overview is None:
            raise RuntimeError(f"CSV job not found: {job_id}")
        job = overview["job"]
        rows = overview["items"]
        tasks = overview["tasks"]
        serialized_overview = self.job_overview(job_id) or {}
        serialized_items = list(serialized_overview.get("items") or [])
        if not serialized_items:
            serialized_items = []
            for item in rows:
                entry = self.repo.get_entry(item.entry_id)
                serialized_items.append(
                    {
                        "row_index": item.row_index,
                        "word": entry.word if entry else "",
                        "part_of_sentence": entry.part_of_sentence if entry else "",
                        "category": entry.category if entry else "",
                        "status": item.status,
                        "shadow_run_id": item.shadow_run_id,
                        "base_regular_asset_id": item.base_regular_asset_id,
                        "base_soften_asset_id": item.base_soften_asset_id,
                        "base_white_bg_asset_id": item.base_white_bg_asset_id,
                    }
                )
        export_dir = exports_root() / sanitize_filename(job.id)
        export_dir.mkdir(parents=True, exist_ok=True)
        summary_csv = export_dir / "job_summary.csv"
        images_csv = export_dir / "images.csv"
        prompts_csv = export_dir / "prompts.csv"
        readme_path = export_dir / "README.md"
        legacy_inventory_csv = export_dir / "word_inventory_legacy.csv"
        manifest_path = export_dir / "manifest.json"
        zip_filename = self.export_zip_name(job.batch_id)
        zip_path = export_dir / zip_filename

        task_by_item: dict[str, list[CsvTaskNode]] = {}
        for task in tasks:
            task_by_item.setdefault(task.csv_job_item_id, []).append(task)

        with summary_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "row_index",
                    "word",
                    "part_of_sentence",
                    "category",
                    "status",
                    "progress",
                    "current_step",
                    "sub_status",
                    "shadow_run_id",
                    "base_regular_asset_id",
                    "base_soften_asset_id",
                    "base_white_bg_asset_id",
                ],
            )
            writer.writeheader()
            for item in serialized_items:
                progress = item.get("progress") or {}
                writer.writerow(
                    {
                        "row_index": item.get("row_index") or "",
                        "word": item.get("word") or "",
                        "part_of_sentence": item.get("part_of_sentence") or "",
                        "category": item.get("category") or "",
                        "status": item.get("main_status") or item.get("status") or "",
                        "progress": f"{progress.get('completed', 0)}/{progress.get('total', 0)}",
                        "current_step": item.get("current_step") or "",
                        "sub_status": item.get("sub_status") or "",
                        "shadow_run_id": item.get("shadow_run_id") or "",
                        "base_regular_asset_id": item.get("base_regular_asset_id") or "",
                        "base_soften_asset_id": item.get("base_soften_asset_id") or "",
                        "base_white_bg_asset_id": item.get("base_white_bg_asset_id") or "",
                    }
                )

        inventory_rows = inventory_service.build_export_rows(job_id)
        selected_export_fields = normalize_csv_job_export_fields(export_fields)
        legacy_inventory_fieldnames = list(selected_export_fields)
        with legacy_inventory_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=legacy_inventory_fieldnames)
            writer.writeheader()
            for row in inventory_rows:
                writer.writerow({field: row.get(field, "") for field in legacy_inventory_fieldnames})

        selected_image_fields = self._selected_export_image_fields(selected_export_fields)
        source_paths = [
            str(row.get(field_name) or "").strip()
            for row in inventory_rows
            for field_name in selected_image_fields
            if str(row.get(field_name) or "").strip()
        ]
        assets_by_path = self.repo.get_assets_by_abs_paths(source_paths)
        image_rows: list[dict[str, Any]] = []
        prompt_rows: list[dict[str, Any]] = []
        image_zip_members: list[tuple[Path, str]] = []
        seen_arcnames: set[str] = set()
        for row in inventory_rows:
            row_index = int(row.get("row_index") or 0)
            word = str(row.get("word") or "").strip()
            part_of_sentence = str(row.get("part_of_sentence") or "").strip()
            category = str(row.get("category") or "").strip()
            context = str(row.get("context") or "").strip()
            for field_name in selected_image_fields:
                source_path = str(row.get(field_name) or "").strip()
                if not source_path:
                    continue
                profile = self._parse_export_image_field(field_name)
                if profile is None:
                    export_warnings.append(f"Skipped unsupported image field {field_name} for row {row_index}")
                    continue
                variant_abbrev = self._export_variant_abbrev(**profile)
                image_filename = self._export_image_filename(
                    row_index=row_index,
                    word=word,
                    part_of_sentence=part_of_sentence,
                    category=category,
                    variant_abbrev=variant_abbrev,
                    source_path=source_path,
                )
                image_relative_path = self._export_image_relative_path(
                    background_type=profile["background_type"],
                    image_filename=image_filename,
                )
                if image_relative_path in seen_arcnames:
                    continue
                try:
                    materialized = materialize_path(source_path, cache_namespace="csv_job_export")
                except Exception as exc:  # noqa: BLE001
                    export_warnings.append(f"Skipped {field_name} for row {row_index}: {exc}")
                    continue
                if not materialized.exists():
                    export_warnings.append(f"Skipped {field_name} for row {row_index}: file not found")
                    continue

                asset = assets_by_path.get(source_path)
                prompt_field = field_name.removesuffix("_path") + "_prompt"
                image_row = {
                    "row_index": row_index,
                    "word": word,
                    "part_of_sentence": part_of_sentence,
                    "category": category,
                    "context": context,
                    "gender": profile["gender"],
                    "age": profile["age"],
                    "skin_color": profile["skin_color"],
                    "background_type": profile["background_type"],
                    "variant_abbrev": variant_abbrev,
                    "image_filename": image_filename,
                    "image_relative_path": image_relative_path,
                    "job_status": row.get("job_status", ""),
                    "fully_complete": row.get("fully_complete", ""),
                    "missing_slots_json": row.get("missing_slots_json", "[]"),
                    "failure_reasons_json": row.get("failure_reasons_json", "[]"),
                }
                prompt_row = {
                    "row_index": row_index,
                    "word": word,
                    "part_of_sentence": part_of_sentence,
                    "category": category,
                    "gender": profile["gender"],
                    "age": profile["age"],
                    "skin_color": profile["skin_color"],
                    "background_type": profile["background_type"],
                    "image_filename": image_filename,
                    "image_relative_path": image_relative_path,
                    "asset_id": asset.id if asset is not None else "",
                    "prompt_stage": self._export_prompt_stage(profile["background_type"]),
                    "prompt_text": row.get(prompt_field, ""),
                    "source_storage_path": source_path,
                }
                image_rows.append(image_row)
                prompt_rows.append(prompt_row)
                image_zip_members.append((materialized, image_relative_path))
                seen_arcnames.add(image_relative_path)

        with images_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=EXPORT_IMAGES_CSV_FIELDS)
            writer.writeheader()
            for row in image_rows:
                writer.writerow({field: row.get(field, "") for field in EXPORT_IMAGES_CSV_FIELDS})

        with prompts_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=EXPORT_PROMPTS_CSV_FIELDS)
            writer.writeheader()
            for row in prompt_rows:
                writer.writerow({field: row.get(field, "") for field in EXPORT_PROMPTS_CSV_FIELDS})

        readme_path.write_text(self._export_readme_text(job.batch_id), encoding="utf-8")

        manifest_payload = {
            "job": self._serialize_job(job, overview),
            "selected_export_fields": selected_export_fields,
            "export_warnings": export_warnings,
            "primary_files": {
                "image_index": "images.csv",
                "prompt_index": "prompts.csv",
                "readme": "README.md",
            },
            "metadata_files": {
                "job_summary": "_metadata/job_summary.csv",
                "legacy_word_inventory": "_metadata/word_inventory_legacy.csv",
                "manifest": "_metadata/manifest.json",
            },
            "image_count": len(image_rows),
            "step_counts": overview.get("step_counts", {}),
            "issues_by_step": overview.get("issues_by_step", {}),
            "items": [
                {
                    "id": item.id,
                    "row_index": item.row_index,
                    "entry_id": item.entry_id,
                    "status": item.status,
                    "shadow_run_id": item.shadow_run_id,
                    "tasks": [
                        {
                            "task_id": task.id,
                            "step_name": task.step_name,
                            "profile_key": task.profile_key,
                            "status": task.status,
                            "regular_asset_id": task.regular_asset_id,
                            "white_bg_asset_id": task.white_bg_asset_id,
                            "error_summary": task.error_summary,
                        }
                        for task in task_by_item.get(item.id, [])
                    ],
                }
                for item in rows
            ],
        }
        zip_members: list[tuple[Path, str]] = [
            (readme_path, "README.md"),
            (images_csv, "images.csv"),
            (prompts_csv, "prompts.csv"),
            (summary_csv, "_metadata/job_summary.csv"),
            (legacy_inventory_csv, "_metadata/word_inventory_legacy.csv"),
            *image_zip_members,
        ]

        manifest_path.write_text(
            json.dumps(manifest_payload, ensure_ascii=False, indent=2, default=_json_default),
            encoding="utf-8",
        )

        with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(manifest_path, arcname="_metadata/manifest.json")
            for source_path, arcname in zip_members:
                archive.write(source_path, arcname=arcname)

        stored_zip = persist_export_artifact(job.id, zip_filename, zip_path.read_bytes(), content_type="application/zip")
        persist_export_artifact(job.id, "README.md", readme_path.read_bytes(), content_type="text/markdown")
        persist_export_artifact(job.id, "images.csv", images_csv.read_bytes(), content_type="text/csv")
        persist_export_artifact(job.id, "prompts.csv", prompts_csv.read_bytes(), content_type="text/csv")
        persist_export_artifact(job.id, "job_summary.csv", summary_csv.read_bytes(), content_type="text/csv")
        persist_export_artifact(job.id, "word_inventory.csv", legacy_inventory_csv.read_bytes(), content_type="text/csv")
        persist_export_artifact(job.id, "word_inventory_legacy.csv", legacy_inventory_csv.read_bytes(), content_type="text/csv")
        persist_export_artifact(job.id, "manifest.json", manifest_path.read_bytes(), content_type="application/json")
        return {
            "job_id": job.id,
            "batch_id": job.batch_id,
            "zip_path": stored_zip.persisted_path,
            "local_zip_path": zip_path.as_posix(),
            "file_name": zip_filename,
        }
