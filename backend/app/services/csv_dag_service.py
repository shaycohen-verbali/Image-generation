from __future__ import annotations

import csv
import json
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import Asset, CsvJob, CsvJobItem, CsvTaskNode, Entry, Run
from app.schemas import ExecutionMode
from app.services.csv_service import parse_entries_csv, validate_entry_row
from app.services.person_profiles import DEFAULT_AGE, DEFAULT_GENDER, DEFAULT_SKIN_COLOR, profile_key
from app.services.pipeline import PipelineRunner
from app.services.repository import Repository
from app.services.storage import exports_root, materialize_path, persist_csv_source, persist_export_artifact
from app.services.utils import sanitize_filename


def _generated_batch_id() -> str:
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    return f"csv_{stamp}_{uuid4().hex[:6]}"


def _parse_profile_key(value: str) -> dict[str, str]:
    gender, age, skin_color = (str(value or "").split(":") + ["", "", ""])[:3]
    return {"gender": gender, "age": age, "skin_color": skin_color}


def _row_task_key(item_id: str, step_name: str, profile: dict[str, str]) -> str:
    return f"{item_id}:{step_name}:{profile_key(profile)}"


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
        }

    def _build_task_specs(self, item: CsvJobItem, entry: Entry) -> list[dict[str, Any]]:
        gender_options = json.loads(entry.person_gender_options_json)
        age_options = json.loads(entry.person_age_options_json)
        skin_options = json.loads(entry.person_skin_color_options_json)

        specs: list[dict[str, Any]] = []
        base_profile = {"gender": DEFAULT_GENDER, "age": DEFAULT_AGE, "skin_color": DEFAULT_SKIN_COLOR}
        base_spec = {
            "step_name": "step1_base",
            "task_key": _row_task_key(item.id, "step1_base", base_profile),
            "profile": base_profile,
            "source_profile": {},
            "branch_role": "base_profile",
            "dependency_keys": [],
            "dependency_task_ids": [],
        }
        specs.append(base_spec)

        for age in age_options:
            if age == DEFAULT_AGE:
                continue
            profile = {"gender": DEFAULT_GENDER, "age": age, "skin_color": DEFAULT_SKIN_COLOR}
            spec = {
                "step_name": "step2_male_age",
                "task_key": _row_task_key(item.id, "step2_male_age", profile),
                "profile": profile,
                "source_profile": base_profile,
                "branch_role": "male_age_variant",
                "dependency_keys": [base_spec["task_key"]],
                "dependency_task_ids": [],
            }
            specs.append(spec)

        if "female" in gender_options:
            female_kid = {"gender": "female", "age": DEFAULT_AGE, "skin_color": DEFAULT_SKIN_COLOR}
            spec = {
                "step_name": "step3_female_white",
                "task_key": _row_task_key(item.id, "step3_female_white", female_kid),
                "profile": female_kid,
                "source_profile": base_profile,
                "branch_role": "female_seed",
                "dependency_keys": [base_spec["task_key"]],
                "dependency_task_ids": [],
            }
            specs.append(spec)

            for age in age_options:
                if age == DEFAULT_AGE:
                    continue
                male_source = {"gender": DEFAULT_GENDER, "age": age, "skin_color": DEFAULT_SKIN_COLOR}
                profile = {"gender": "female", "age": age, "skin_color": DEFAULT_SKIN_COLOR}
                spec = {
                    "step_name": "step3_female_white",
                    "task_key": _row_task_key(item.id, "step3_female_white", profile),
                    "profile": profile,
                    "source_profile": male_source,
                    "branch_role": "female_age_variant",
                    "dependency_keys": [_row_task_key(item.id, "step2_male_age", male_source)],
                    "dependency_task_ids": [],
                }
                specs.append(spec)

        for skin_color in skin_options:
            if skin_color == DEFAULT_SKIN_COLOR:
                continue
            for gender in gender_options:
                for age in age_options:
                    target = {"gender": gender, "age": age, "skin_color": skin_color}
                    source = {"gender": gender, "age": age, "skin_color": DEFAULT_SKIN_COLOR}
                    source_step = "step1_base" if gender == DEFAULT_GENDER and age == DEFAULT_AGE else (
                        "step2_male_age" if gender == DEFAULT_GENDER else "step3_female_white"
                    )
                    spec = {
                        "step_name": "step4_race_variant",
                        "task_key": _row_task_key(item.id, "step4_race_variant", target),
                        "profile": target,
                        "source_profile": source,
                        "branch_role": "appearance_variant",
                        "dependency_keys": [_row_task_key(item.id, source_step, source)],
                        "dependency_task_ids": [],
                    }
                    specs.append(spec)

        task_id_by_key: dict[str, str] = {}
        created_specs: list[dict[str, Any]] = []
        for spec in specs:
            dependency_keys = [key for key in spec["dependency_keys"] if key]
            node = self.repo.create_csv_task_node(
                csv_job_id=item.csv_job_id,
                csv_job_item_id=item.id,
                step_name=spec["step_name"],
                task_key=spec["task_key"],
                profile_key=profile_key(spec["profile"]),
                source_profile_key=profile_key(spec["source_profile"]) if spec["source_profile"] else "",
                branch_role=spec["branch_role"],
                dependency_keys=dependency_keys,
                dependency_task_ids=[],
                status="pending",
            )
            task_id_by_key[spec["task_key"]] = node.id
            created_specs.append({**spec, "id": node.id})

        for spec in created_specs:
            dependency_ids = [task_id_by_key[key] for key in spec["dependency_keys"] if key in task_id_by_key]
            if dependency_ids:
                task = self.repo.get_csv_task(spec["id"])
                if task is not None:
                    self.repo.update_csv_task(task, dependency_task_ids_json=json.dumps(dependency_ids, ensure_ascii=True))
        return created_specs

    def import_csv_job(
        self,
        *,
        file_name: str,
        content: bytes,
        execution_mode: ExecutionMode,
        person_gender_options: list[str],
        person_age_options: list[str],
        person_skin_color_options: list[str],
    ) -> dict[str, Any]:
        if execution_mode != "csv_dag":
            raise RuntimeError("CsvDagService only supports csv_dag execution mode")

        rows = parse_entries_csv(content)
        batch_id = _generated_batch_id()
        snapshot = self._runtime_snapshot(
            person_gender_options=person_gender_options,
            person_age_options=person_age_options,
            person_skin_color_options=person_skin_color_options,
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
            entry = self.repo.create_entry(payload)
            item = self.repo.create_csv_job_item(
                csv_job_id=job.id,
                entry_id=entry.id,
                row_index=index,
                source_row=row,
            )
            self._build_task_specs(item, entry)
            imported_count += 1
            results.append({"row_index": index, "status": "imported", "entry_id": entry.id})

        if imported_count == 0:
            self.repo.update_csv_job(job, status="failed", error_detail="No valid CSV rows were imported", finished_at=datetime.utcnow())
        return {
            "job_id": job.id,
            "batch_id": batch_id,
            "status": self.repo.get_csv_job(job.id).status if self.repo.get_csv_job(job.id) else job.status,
            "imported_count": imported_count,
            "skipped_count": skipped_count,
            "execution_mode": execution_mode,
            "rows": results,
        }

    def list_jobs(self) -> list[dict[str, Any]]:
        jobs = self.repo.list_csv_jobs()
        output: list[dict[str, Any]] = []
        for job in jobs:
            overview = self.repo.csv_job_overview(job.id) or {}
            output.append(self._serialize_job(job, overview))
        return output

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        overview = self.repo.csv_job_overview(job_id)
        if overview is None:
            return None
        return self._serialize_job(overview["job"], overview)

    def start_job(self, job_id: str) -> CsvJob:
        job = self.repo.get_csv_job(job_id)
        if job is None:
            raise RuntimeError(f"CSV job not found: {job_id}")
        for task in self.repo.list_csv_tasks(job_id):
            if task.status == "pending":
                self.repo.update_csv_task(task, status="queued", error_summary="", finished_at=None)
        return self.repo.update_csv_job(job, status="queued", error_detail="", finished_at=None)

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
            next_status = "canceled" if all(status == "canceled" for status in statuses) else "queued"
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
            if task.step_name == "step1_base":
                completed_run = runner.process_base_run(shadow_run.id)
                winner_attempt = max(1, int(completed_run.optimization_attempt or 1))
                regular_asset = next(
                    (asset for asset in self.repo.run_snapshot(shadow_run.id)[2] if asset.stage_name == "stage3_upgraded" and int(asset.attempt or 0) == winner_attempt),
                    None,
                )
                white_bg_asset = next(
                    (asset for asset in self.repo.run_snapshot(shadow_run.id)[2] if asset.stage_name == "stage4_white_bg" and int(asset.attempt or 0) == winner_attempt),
                    None,
                )
                if regular_asset is None or white_bg_asset is None:
                    raise RuntimeError("Base DAG task completed without both regular and white-background assets")
                self.repo.add_csv_task_attempt(
                    csv_task_node_id=task.id,
                    attempt_number=attempt_number,
                    status="completed",
                    request_json={"step_name": task.step_name, "shadow_run_id": shadow_run.id},
                    response_json={
                        "winner_attempt": winner_attempt,
                        "regular_asset_id": regular_asset.id,
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
                    base_white_bg_asset_id=white_bg_asset.id,
                )
            else:
                dependency_ids = [str(value) for value in json.loads(task.dependency_task_ids_json or "[]") if str(value)]
                if not dependency_ids:
                    raise RuntimeError(f"Task {task.task_key} has no dependency task ids")
                source_task = self.repo.get_csv_task(dependency_ids[0])
                if source_task is None or not source_task.regular_asset_id:
                    raise RuntimeError(f"Task {task.task_key} is missing its source asset")
                source_asset = self.repo.get_asset(source_task.regular_asset_id)
                if source_asset is None:
                    raise RuntimeError(f"Missing source asset {source_task.regular_asset_id} for task {task.task_key}")
                winner_attempt = max(1, int((self.repo.get_run(shadow_run.id).optimization_attempt if self.repo.get_run(shadow_run.id) else 1) or 1))
                target_profile = _parse_profile_key(task.profile_key)
                source_profile = _parse_profile_key(task.source_profile_key) if task.source_profile_key else None
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
                )
                regular_asset = created["regular_asset"]
                white_bg_asset = created["white_bg_asset"]
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
                    source_asset_id=source_asset.id,
                    regular_asset_id=regular_asset.id,
                    white_bg_asset_id=white_bg_asset.id,
                    status="completed",
                    error_summary="",
                    finished_at=datetime.utcnow(),
                )
        except Exception as exc:  # noqa: BLE001
            self.repo.add_csv_task_attempt(
                csv_task_node_id=task.id,
                attempt_number=attempt_number,
                status="failed",
                request_json=getattr(exc, "request_json", {}) if isinstance(getattr(exc, "request_json", {}), dict) else {},
                response_json=getattr(exc, "response_json", {}) if isinstance(getattr(exc, "response_json", {}), dict) else {},
                error_detail=str(exc),
                finished_at=datetime.utcnow(),
            )
            finished_task = self.repo.update_csv_task(
                task,
                status="failed",
                error_summary=str(exc),
                finished_at=datetime.utcnow(),
            )
        finally:
            runner.google_images.close()

        self._update_item_status(item)
        self.repo.finalize_csv_job_status(job.id)
        return self.repo.get_csv_task(task.id) or finished_task

    def _serialize_job(self, job: CsvJob, overview: dict[str, Any]) -> dict[str, Any]:
        total_row_count = int(overview.get("total_row_count") or 0)
        duration_seconds = float(overview.get("duration_seconds") or 0)
        return {
            "id": job.id,
            "batch_id": job.batch_id,
            "execution_mode": job.execution_mode,
            "source_file_name": job.source_file_name,
            "status": job.status,
            "error_detail": job.error_detail,
            "total_row_count": total_row_count,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "duration_seconds": duration_seconds,
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

    def job_overview(self, job_id: str) -> dict[str, Any] | None:
        overview = self.repo.csv_job_overview(job_id)
        if overview is None:
            return None
        job = overview["job"]
        items_payload: list[dict[str, Any]] = []
        for item in overview["items"]:
            entry = self.repo.get_entry(item.entry_id)
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
                    "base_regular_asset_id": item.base_regular_asset_id,
                    "base_white_bg_asset_id": item.base_white_bg_asset_id,
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
            "export_ready": job.status in {"completed", "failed", "canceled"},
            "export_id": job.id if self.export_local_zip_path(job).exists() else None,
        }

    def export_job(self, job_id: str) -> dict[str, Any]:
        overview = self.repo.csv_job_overview(job_id)
        if overview is None:
            raise RuntimeError(f"CSV job not found: {job_id}")
        job = overview["job"]
        rows = overview["items"]
        tasks = overview["tasks"]
        export_dir = exports_root() / sanitize_filename(job.id)
        export_dir.mkdir(parents=True, exist_ok=True)
        summary_csv = export_dir / "job_summary.csv"
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
                    "shadow_run_id",
                    "base_regular_asset_id",
                    "base_white_bg_asset_id",
                ],
            )
            writer.writeheader()
            for item in rows:
                entry = self.repo.get_entry(item.entry_id)
                writer.writerow(
                    {
                        "row_index": item.row_index,
                        "word": entry.word if entry else "",
                        "part_of_sentence": entry.part_of_sentence if entry else "",
                        "category": entry.category if entry else "",
                        "status": item.status,
                        "shadow_run_id": item.shadow_run_id or "",
                        "base_regular_asset_id": item.base_regular_asset_id or "",
                        "base_white_bg_asset_id": item.base_white_bg_asset_id or "",
                    }
                )

        manifest_payload = {
            "job": self._serialize_job(job, overview),
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
        manifest_path.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(summary_csv, arcname="job_summary.csv")
            archive.write(manifest_path, arcname="manifest.json")
            for item in rows:
                entry = self.repo.get_entry(item.entry_id)
                prefix = f"row_{item.row_index:04d}_{sanitize_filename(entry.word if entry else item.id)}"
                for task in task_by_item.get(item.id, []):
                    if task.regular_asset_id:
                        regular_asset = self.repo.get_asset(task.regular_asset_id)
                        if regular_asset is not None:
                            regular_path = materialize_path(regular_asset.abs_path, cache_namespace="csv_job_export")
                            archive.write(regular_path, arcname=f"regular/{prefix}/{regular_asset.file_name}")
                    if task.white_bg_asset_id:
                        white_bg_asset = self.repo.get_asset(task.white_bg_asset_id)
                        if white_bg_asset is not None:
                            white_bg_path = materialize_path(white_bg_asset.abs_path, cache_namespace="csv_job_export")
                            archive.write(white_bg_path, arcname=f"white_bg/{prefix}/{white_bg_asset.file_name}")

        stored_zip = persist_export_artifact(job.id, zip_filename, zip_path.read_bytes(), content_type="application/zip")
        persist_export_artifact(job.id, "job_summary.csv", summary_csv.read_bytes(), content_type="text/csv")
        persist_export_artifact(job.id, "manifest.json", manifest_path.read_bytes(), content_type="application/json")
        return {
            "job_id": job.id,
            "batch_id": job.batch_id,
            "zip_path": stored_zip.persisted_path,
            "local_zip_path": zip_path.as_posix(),
            "file_name": zip_filename,
        }
