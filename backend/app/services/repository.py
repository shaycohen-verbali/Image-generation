from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from sqlalchemy import Select, desc, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Asset,
    CsvJob,
    CsvJobItem,
    CsvTaskAttempt,
    CsvTaskNode,
    Entry,
    Export,
    Prompt,
    Run,
    RunEvent,
    RuntimeConfig,
    Score,
    StageResult,
)
from app.services.model_catalog import (
    normalize_image_aspect_ratio,
    normalize_image_format,
    normalize_image_resolution,
    normalize_nano_banana_safety_level,
    normalize_prompt_engineer_model,
    normalize_stage3_generation_model,
    normalize_vision_model,
)
from app.services.person_profiles import (
    DEFAULT_AGE,
    DEFAULT_GENDER,
    DEFAULT_SKIN_COLOR,
    dump_option_set,
    normalize_option_set,
)
from app.services.prompt_templates import (
    DEFAULT_STAGE1_PROMPT_TEMPLATE,
    DEFAULT_STAGE3_PROMPT_TEMPLATE,
    DEFAULT_VISUAL_STYLE_ID,
    DEFAULT_VISUAL_STYLE_NAME,
    DEFAULT_VISUAL_STYLE_PROMPT_BLOCK,
)
from app.services.utils import deterministic_entry_id, source_row_hash

MIN_QUALITY_THRESHOLD = 95
MIN_PARALLEL_RUNS = 1
MIN_VARIANT_WORKERS = 1


def _dumps(value: dict[str, Any] | list[Any]) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def _loads(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _loads_list(value: str) -> list[Any]:
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


class Repository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _release_instance(self, instance):
        try:
            self.db.expunge(instance)
        except Exception:  # noqa: BLE001
            pass
        return instance

    def _managed_instance(self, instance):
        identity = getattr(instance, "id", None)
        if not identity:
            return instance
        managed = self.db.get(type(instance), identity)
        if managed is not None:
            return managed
        return instance

    def get_runtime_config(self) -> RuntimeConfig:
        config = self.db.execute(select(RuntimeConfig).where(RuntimeConfig.id == 1)).scalar_one_or_none()
        if config is None:
            raise RuntimeError("Runtime config not initialized")
        return config

    def update_runtime_config(self, updates: dict[str, Any]) -> RuntimeConfig:
        config = self.get_runtime_config()
        for key, value in updates.items():
            if value is not None and hasattr(config, key):
                setattr(config, key, value)
        if config.prompt_engineer_mode not in {"assistant", "responses_api"}:
            config.prompt_engineer_mode = "responses_api"
        if updates.get("openai_model_vision") is not None:
            legacy_model = normalize_vision_model(config.openai_model_vision)
            if updates.get("stage3_critique_model") is None:
                config.stage3_critique_model = legacy_model
            if updates.get("quality_gate_model") is None:
                config.quality_gate_model = legacy_model
        config.responses_prompt_engineer_model = normalize_prompt_engineer_model(config.responses_prompt_engineer_model)
        config.responses_vector_store_id = str(config.responses_vector_store_id or "").strip()
        config.visual_style_id = str(config.visual_style_id or DEFAULT_VISUAL_STYLE_ID).strip() or DEFAULT_VISUAL_STYLE_ID
        config.visual_style_name = str(config.visual_style_name or DEFAULT_VISUAL_STYLE_NAME).strip() or DEFAULT_VISUAL_STYLE_NAME
        config.visual_style_prompt_block = str(config.visual_style_prompt_block or DEFAULT_VISUAL_STYLE_PROMPT_BLOCK).strip() or DEFAULT_VISUAL_STYLE_PROMPT_BLOCK
        config.stage1_prompt_template = str(config.stage1_prompt_template or DEFAULT_STAGE1_PROMPT_TEMPLATE)
        config.stage3_prompt_template = str(config.stage3_prompt_template or DEFAULT_STAGE3_PROMPT_TEMPLATE)
        config.stage3_critique_model = normalize_vision_model(config.stage3_critique_model)
        config.stage3_generate_model = normalize_stage3_generation_model(config.stage3_generate_model)
        config.quality_gate_model = normalize_vision_model(config.quality_gate_model)
        config.image_aspect_ratio = normalize_image_aspect_ratio(getattr(config, "image_aspect_ratio", "1:1"))
        config.image_resolution = normalize_image_resolution(getattr(config, "image_resolution", "1K"))
        config.image_format = normalize_image_format(getattr(config, "image_format", "image/jpeg"))
        config.nano_banana_safety_level = normalize_nano_banana_safety_level(
            getattr(config, "nano_banana_safety_level", "default")
        )
        config.openai_model_vision = config.stage3_critique_model
        config.quality_threshold = max(MIN_QUALITY_THRESHOLD, int(config.quality_threshold))
        config.max_parallel_runs = max(MIN_PARALLEL_RUNS, int(config.max_parallel_runs))
        config.max_variant_workers = max(MIN_VARIANT_WORKERS, int(config.max_variant_workers))
        self.db.add(config)
        self.db.commit()
        self.db.refresh(config)
        return config

    def create_entry(self, payload: dict[str, Any]) -> Entry:
        entry_id = deterministic_entry_id(payload["word"], payload["part_of_sentence"], payload["category"])
        row_hash = source_row_hash(payload)

        existing = self.db.execute(select(Entry).where(Entry.id == entry_id)).scalar_one_or_none()
        if existing:
            gender_options = normalize_option_set(payload.get("person_gender_options", []), ("male", "female"), DEFAULT_GENDER)
            age_options = normalize_option_set(payload.get("person_age_options", []), ("toddler", "kid", "tween", "teenager"), DEFAULT_AGE)
            skin_options = normalize_option_set(payload.get("person_skin_color_options", []), ("white", "black", "asian", "brown"), DEFAULT_SKIN_COLOR)
            existing.context = payload.get("context", "").strip()
            existing.boy_or_girl = gender_options[0]
            existing.person_gender_options_json = dump_option_set(gender_options)
            existing.person_age_options_json = dump_option_set(age_options)
            existing.person_skin_color_options_json = dump_option_set(skin_options)
            existing.batch = str(payload.get("batch", "")).strip()
            existing.source_row_hash = row_hash
            self.db.add(existing)
            self.db.commit()
            self.db.refresh(existing)
            return existing

        gender_options = normalize_option_set(payload.get("person_gender_options", []), ("male", "female"), DEFAULT_GENDER)
        age_options = normalize_option_set(payload.get("person_age_options", []), ("toddler", "kid", "tween", "teenager"), DEFAULT_AGE)
        skin_options = normalize_option_set(payload.get("person_skin_color_options", []), ("white", "black", "asian", "brown"), DEFAULT_SKIN_COLOR)
        entry = Entry(
            id=entry_id,
            word=payload["word"].strip(),
            part_of_sentence=payload["part_of_sentence"].strip(),
            category=payload["category"].strip(),
            context=payload.get("context", "").strip(),
            boy_or_girl=gender_options[0],
            person_gender_options_json=dump_option_set(gender_options),
            person_age_options_json=dump_option_set(age_options),
            person_skin_color_options_json=dump_option_set(skin_options),
            batch=str(payload.get("batch", "")).strip(),
            source_row_hash=row_hash,
        )
        self.db.add(entry)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            entry = self.db.execute(select(Entry).where(Entry.id == entry_id)).scalar_one()
            return entry

        self.db.refresh(entry)
        return entry

    def create_entry_uncommitted(self, payload: dict[str, Any]) -> Entry:
        entry_id = deterministic_entry_id(payload["word"], payload["part_of_sentence"], payload["category"])
        row_hash = source_row_hash(payload)
        gender_options = normalize_option_set(payload.get("person_gender_options", []), ("male", "female"), DEFAULT_GENDER)
        age_options = normalize_option_set(payload.get("person_age_options", []), ("toddler", "kid", "tween", "teenager"), DEFAULT_AGE)
        skin_options = normalize_option_set(payload.get("person_skin_color_options", []), ("white", "black", "asian", "brown"), DEFAULT_SKIN_COLOR)
        existing = self.db.get(Entry, entry_id)
        if existing is None:
            with self.db.no_autoflush:
                existing = self.db.execute(select(Entry).where(Entry.id == entry_id)).scalar_one_or_none()
        if existing:
            existing.context = payload.get("context", "").strip()
            existing.boy_or_girl = gender_options[0]
            existing.person_gender_options_json = dump_option_set(gender_options)
            existing.person_age_options_json = dump_option_set(age_options)
            existing.person_skin_color_options_json = dump_option_set(skin_options)
            existing.batch = str(payload.get("batch", "")).strip()
            existing.source_row_hash = row_hash
            self.db.add(existing)
            return existing

        entry = Entry(
            id=entry_id,
            word=payload["word"].strip(),
            part_of_sentence=payload["part_of_sentence"].strip(),
            category=payload["category"].strip(),
            context=payload.get("context", "").strip(),
            boy_or_girl=gender_options[0],
            person_gender_options_json=dump_option_set(gender_options),
            person_age_options_json=dump_option_set(age_options),
            person_skin_color_options_json=dump_option_set(skin_options),
            batch=str(payload.get("batch", "")).strip(),
            source_row_hash=row_hash,
        )
        self.db.add(entry)
        return entry

    def update_entries_profile_options(
        self,
        *,
        entry_ids: list[str],
        person_gender_options: list[str],
        person_age_options: list[str],
        person_skin_color_options: list[str],
    ) -> int:
        ids = [str(entry_id or "").strip() for entry_id in entry_ids if str(entry_id or "").strip()]
        if not ids:
            return 0
        entries = list(self.db.execute(select(Entry).where(Entry.id.in_(ids))).scalars())
        if not entries:
            return 0
        gender_options = normalize_option_set(person_gender_options, ("male", "female"), DEFAULT_GENDER)
        age_options = normalize_option_set(person_age_options, ("toddler", "kid", "tween", "teenager"), DEFAULT_AGE)
        skin_options = normalize_option_set(person_skin_color_options, ("white", "black", "asian", "brown"), DEFAULT_SKIN_COLOR)
        for entry in entries:
            entry.boy_or_girl = gender_options[0]
            entry.person_gender_options_json = dump_option_set(gender_options)
            entry.person_age_options_json = dump_option_set(age_options)
            entry.person_skin_color_options_json = dump_option_set(skin_options)
            self.db.add(entry)
        self.db.commit()
        return len(entries)

    def list_entries(
        self,
        *,
        word: str | None = None,
        part_of_sentence: str | None = None,
        category: str | None = None,
        batch: str | None = None,
        status: str | None = None,
        min_score: float | None = None,
        max_score: float | None = None,
    ) -> list[tuple[Entry, Run | None]]:
        stmt: Select[tuple[Entry]] = select(Entry)
        if word:
            stmt = stmt.where(Entry.word.ilike(f"%{word}%"))
        if part_of_sentence:
            stmt = stmt.where(Entry.part_of_sentence == part_of_sentence)
        if category:
            stmt = stmt.where(Entry.category == category)
        if batch:
            stmt = stmt.where(Entry.batch == batch)

        entries = list(self.db.execute(stmt.order_by(Entry.word.asc())).scalars())
        output: list[tuple[Entry, Run | None]] = []
        for entry in entries:
            latest_run = self.db.execute(
                select(Run).where(Run.entry_id == entry.id).order_by(desc(Run.created_at)).limit(1)
            ).scalar_one_or_none()
            if status and (latest_run is None or latest_run.status != status):
                continue
            if min_score is not None and (latest_run is None or latest_run.quality_score is None or latest_run.quality_score < min_score):
                continue
            if max_score is not None and (latest_run is None or latest_run.quality_score is None or latest_run.quality_score > max_score):
                continue
            output.append((entry, latest_run))
        return output

    def create_runs(
        self,
        entry_ids: list[str],
        *,
        quality_threshold: int,
        max_optimization_attempts: int,
        execution_mode: str = "legacy",
    ) -> list[Run]:
        threshold = max(MIN_QUALITY_THRESHOLD, int(quality_threshold))
        runs: list[Run] = []
        for entry_id in entry_ids:
            run = Run(
                entry_id=entry_id,
                execution_mode=execution_mode,
                status="queued",
                current_stage="queued",
                quality_threshold=threshold,
                max_optimization_attempts=max_optimization_attempts,
            )
            self.db.add(run)
            runs.append(run)
        self.db.commit()
        for run in runs:
            self.db.refresh(run)
        return runs

    def get_run(self, run_id: str) -> Run | None:
        return self.db.execute(select(Run).where(Run.id == run_id)).scalar_one_or_none()

    def get_runs_by_ids(self, run_ids: list[str]) -> dict[str, Run]:
        normalized = [str(value or "").strip() for value in run_ids if str(value or "").strip()]
        if not normalized:
            return {}
        rows = list(self.db.execute(select(Run).where(Run.id.in_(normalized))).scalars())
        return {row.id: row for row in rows}

    def list_runs(
        self,
        *,
        status: str | None = None,
        entry_id: str | None = None,
        min_score: float | None = None,
        max_score: float | None = None,
    ) -> list[Run]:
        stmt = select(Run)
        if status:
            stmt = stmt.where(Run.status == status)
        if entry_id:
            stmt = stmt.where(Run.entry_id == entry_id)
        if min_score is not None:
            stmt = stmt.where(Run.quality_score >= min_score)
        if max_score is not None:
            stmt = stmt.where(Run.quality_score <= max_score)
        stmt = stmt.where(Run.execution_mode == "legacy")
        stmt = stmt.order_by(desc(Run.created_at))
        return list(self.db.execute(stmt).scalars())

    def get_entry(self, entry_id: str) -> Entry | None:
        return self.db.execute(select(Entry).where(Entry.id == entry_id)).scalar_one_or_none()

    def get_entries_by_ids(self, entry_ids: list[str]) -> dict[str, Entry]:
        normalized = [str(value or "").strip() for value in entry_ids if str(value or "").strip()]
        if not normalized:
            return {}
        rows = list(self.db.execute(select(Entry).where(Entry.id.in_(normalized))).scalars())
        return {row.id: row for row in rows}

    def update_entry_has_person(self, entry_id: str, has_person: str) -> None:
        entry = self.get_entry(entry_id)
        if entry is None:
            return
        entry.has_person = has_person
        self.db.add(entry)
        self.db.commit()

    def batch_job_summary(self, batch_id: str) -> dict[str, Any] | None:
        batch = str(batch_id or "").strip()
        if not batch:
            return None

        rows = list(
            self.db.execute(
                select(Run, Entry)
                .join(Entry, Entry.id == Run.entry_id)
                .where(Entry.batch == batch)
                .where(Run.execution_mode == "legacy")
                .order_by(Run.created_at.asc())
            )
        )
        if not rows:
            return None

        runs = [run for run, _entry in rows]
        terminal_statuses = {"completed_pass", "completed_fail_threshold", "failed_technical", "canceled"}
        completed_statuses = {"completed_pass", "completed_fail_threshold"}
        passed_runs = [run for run in runs if run.status == "completed_pass"]
        below_threshold_runs = [run for run in runs if run.status == "completed_fail_threshold"]
        failed_technical_runs = [run for run in runs if run.status == "failed_technical"]
        canceled_runs = [run for run in runs if run.status == "canceled"]
        terminal_runs = [run for run in runs if run.status in terminal_statuses]
        completed_runs = [run for run in runs if run.status in completed_statuses]
        timed_runs = [run for run in runs if run.status != "canceled"] or runs

        started_at = min((run.created_at for run in timed_runs), default=None)
        is_complete = len(terminal_runs) == len(runs)
        timed_terminal_runs = [run for run in timed_runs if run.status in terminal_statuses]
        finished_at = max((run.updated_at for run in timed_terminal_runs), default=None) if is_complete else None
        now = datetime.utcnow()
        duration_end = finished_at or now
        duration_seconds = 0.0
        if started_at is not None:
            duration_seconds = max(0.0, (duration_end - started_at).total_seconds())
        avg_seconds_per_word = duration_seconds / len(timed_runs) if timed_runs else 0.0

        if is_complete:
            status = "completed"
        elif any(run.status == "running" for run in runs):
            status = "running"
        elif any(run.status == "cancel_requested" for run in runs):
            status = "canceling"
        elif any(run.status in {"queued", "retry_queued"} for run in runs):
            status = "queued"
        else:
            status = "pending"

        return {
            "batch_id": batch,
            "status": status,
            "run_count": len(runs),
            "completed_run_count": len(completed_runs),
            "terminal_run_count": len(terminal_runs),
            "passed_run_count": len(passed_runs),
            "below_threshold_run_count": len(below_threshold_runs),
            "failed_technical_run_count": len(failed_technical_runs),
            "canceled_run_count": len(canceled_runs),
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_seconds": duration_seconds,
            "avg_seconds_per_word": avg_seconds_per_word,
            "is_complete": is_complete,
        }

    def batch_job_report(self, batch_id: str) -> dict[str, Any] | None:
        summary = self.batch_job_summary(batch_id)
        if summary is None:
            return None

        batch = str(batch_id or "").strip()
        rows = list(
            self.db.execute(
                select(Run, Entry)
                .join(Entry, Entry.id == Run.entry_id)
                .where(Entry.batch == batch)
                .where(Run.execution_mode == "legacy")
                .order_by(Run.updated_at.desc())
            )
        )
        issues: list[dict[str, Any]] = []
        reason_counts: dict[str, int] = {}
        for run, entry in rows:
            if run.status == "completed_pass":
                continue
            if run.status == "failed_technical":
                reason = str(run.error_detail or "").strip() or "Technical failure"
            elif run.status == "canceled":
                reason = str(run.error_detail or "").strip() or "Stopped by user"
            elif run.status == "completed_fail_threshold":
                score = f"{run.quality_score:.0f}" if run.quality_score is not None else "unknown"
                reason = f"Score below threshold ({score} < {run.quality_threshold})"
            else:
                reason = f"Status: {run.status}"
            issues.append(
                {
                    "run_id": run.id,
                    "entry_id": run.entry_id,
                    "word": entry.word,
                    "part_of_sentence": entry.part_of_sentence,
                    "category": entry.category,
                    "status": run.status,
                    "quality_score": run.quality_score,
                    "error_detail": run.error_detail,
                    "reason": reason,
                    "updated_at": run.updated_at,
                }
            )
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

        return {
            **summary,
            "issues": issues,
            "reason_counts": reason_counts,
        }

    def _remove_run_asset_files(self, run_id: str) -> None:
        assets = list(self.db.execute(select(Asset).where(Asset.run_id == run_id)).scalars())
        for asset in assets:
            path = str(asset.abs_path or "").strip()
            if not path:
                continue
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                continue

    def delete_run(self, run_id: str) -> bool:
        run = self.get_run(run_id)
        if run is None:
            return False
        self._remove_run_asset_files(run.id)
        self.db.delete(run)
        self.db.commit()
        return True

    def clear_terminal_runs(self, *, batch_id: str | None = None) -> list[str]:
        stmt = select(Run)
        if batch_id:
            stmt = stmt.join(Entry, Entry.id == Run.entry_id).where(Entry.batch == str(batch_id).strip())
        terminal_statuses = {"completed_pass", "completed_fail_threshold", "failed_technical", "canceled", "cancel_requested"}
        stmt = stmt.where(Run.status.in_(terminal_statuses))
        runs = list(self.db.execute(stmt).scalars())
        deleted_ids: list[str] = []
        for run in runs:
            self._remove_run_asset_files(run.id)
            deleted_ids.append(run.id)
            self.db.delete(run)
        self.db.commit()
        return deleted_ids

    def claim_next_queued_run(self) -> Run | None:
        candidate = self.db.execute(
            select(Run)
            .where(Run.execution_mode == "legacy")
            .where(Run.status.in_(["queued", "retry_queued"]))
            .order_by(Run.created_at.asc())
            .limit(1)
        ).scalar_one_or_none()
        if candidate is None:
            return None

        updated = self.db.execute(
            update(Run)
            .where(Run.id == candidate.id)
            .where(Run.execution_mode == "legacy")
            .where(Run.status.in_(["queued", "retry_queued"]))
            .values(status="running", current_stage=candidate.retry_from_stage or candidate.current_stage)
        )
        if updated.rowcount == 0:
            self.db.rollback()
            return None

        self.db.commit()
        return self.get_run(candidate.id)

    def request_stop_run(self, run: Run) -> Run:
        current_status = str(run.status or "").strip().lower()
        if current_status in {"completed_pass", "completed_fail_threshold", "failed_technical", "canceled"}:
            return run
        if current_status in {"queued", "retry_queued"}:
            updated = self.db.execute(
                update(Run)
                .where(Run.id == run.id)
                .where(Run.status.in_(["queued", "retry_queued"]))
                .values(
                    status="canceled",
                    current_stage="canceled",
                    retry_from_stage="",
                    error_detail="Stopped by user",
                )
            )
            if updated.rowcount:
                self.db.commit()
                refreshed = self.get_run(run.id)
                return self._release_instance(refreshed) if refreshed is not None else run
            self.db.rollback()
            run = self.get_run(run.id) or run
            current_status = str(run.status or "").strip().lower()
            if current_status in {"completed_pass", "completed_fail_threshold", "failed_technical", "canceled"}:
                return self._release_instance(run)

        if current_status != "cancel_requested":
            run.status = "cancel_requested"
            run.error_detail = "Stop requested by user"
            self.db.add(run)
            self.db.commit()
            self.db.refresh(run)
        return self._release_instance(run)

    def update_run(self, run: Run, **updates: Any) -> Run:
        for key, value in updates.items():
            setattr(run, key, value)
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return self._release_instance(run)

    def add_stage_result(
        self,
        *,
        run_id: str,
        stage_name: str,
        attempt: int,
        status: str,
        idempotency_key: str,
        request_json: dict[str, Any],
        response_json: dict[str, Any],
        error_detail: str = "",
    ) -> StageResult:
        existing = self.db.execute(
            select(StageResult)
            .where(StageResult.run_id == run_id)
            .where(StageResult.stage_name == stage_name)
            .where(StageResult.attempt == attempt)
        ).scalar_one_or_none()
        if existing is not None:
            existing.status = status
            existing.request_json = _dumps(request_json)
            existing.response_json = _dumps(response_json)
            existing.error_detail = error_detail
            self.db.add(existing)
            self.db.commit()
            self.db.refresh(existing)
            return self._release_instance(existing)

        record = StageResult(
            run_id=run_id,
            stage_name=stage_name,
            attempt=attempt,
            status=status,
            idempotency_key=idempotency_key,
            request_json=_dumps(request_json),
            response_json=_dumps(response_json),
            error_detail=error_detail,
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return self._release_instance(record)

    def add_run_event(
        self,
        *,
        run_id: str,
        stage_name: str,
        attempt: int,
        event_type: str,
        status: str,
        message: str,
        payload_json: dict[str, Any] | None = None,
    ) -> RunEvent:
        event = RunEvent(
            run_id=run_id,
            stage_name=stage_name,
            attempt=attempt,
            event_type=event_type,
            status=status,
            message=message,
            payload_json=_dumps(payload_json or {}),
        )
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        return self._release_instance(event)

    def list_run_events(self, run_id: str) -> list[RunEvent]:
        return list(
            self.db.execute(
                select(RunEvent)
                .where(RunEvent.run_id == run_id)
                .order_by(RunEvent.created_at.asc())
            ).scalars()
        )

    def add_prompt(
        self,
        *,
        run_id: str,
        stage_name: str,
        attempt: int,
        prompt_text: str,
        needs_person: str,
        source: str,
        raw_response_json: dict[str, Any],
    ) -> Prompt:
        prompt = Prompt(
            run_id=run_id,
            stage_name=stage_name,
            attempt=attempt,
            prompt_text=prompt_text,
            needs_person=needs_person,
            source=source,
            raw_response_json=_dumps(raw_response_json),
        )
        self.db.add(prompt)
        self.db.commit()
        self.db.refresh(prompt)
        return self._release_instance(prompt)

    def get_prompt_for_stage_attempt(self, *, run_id: str, stage_name: str, attempt: int) -> Prompt | None:
        return self.db.execute(
            select(Prompt)
            .where(Prompt.run_id == run_id)
            .where(Prompt.stage_name == stage_name)
            .where(Prompt.attempt == attempt)
            .order_by(desc(Prompt.created_at))
            .limit(1)
        ).scalar_one_or_none()

    def add_asset(
        self,
        *,
        run_id: str,
        stage_name: str,
        attempt: int,
        file_name: str,
        abs_path: str,
        mime_type: str,
        sha256: str,
        width: int,
        height: int,
        origin_url: str,
        model_name: str,
    ) -> Asset:
        existing = self.db.execute(
            select(Asset)
            .where(Asset.run_id == run_id)
            .where(Asset.stage_name == stage_name)
            .where(Asset.attempt == attempt)
            .where(Asset.file_name == file_name)
            .limit(1)
        ).scalar_one_or_none()
        if existing is not None:
            existing.abs_path = abs_path
            existing.mime_type = mime_type
            existing.sha256 = sha256
            existing.width = width
            existing.height = height
            existing.origin_url = origin_url
            existing.model_name = model_name
            self.db.add(existing)
            self.db.commit()
            self.db.refresh(existing)
            return self._release_instance(existing)

        asset = Asset(
            run_id=run_id,
            stage_name=stage_name,
            attempt=attempt,
            file_name=file_name,
            abs_path=abs_path,
            mime_type=mime_type,
            sha256=sha256,
            width=width,
            height=height,
            origin_url=origin_url,
            model_name=model_name,
        )
        self.db.add(asset)
        self.db.commit()
        self.db.refresh(asset)
        return self._release_instance(asset)

    def get_asset_by_file_name(
        self,
        *,
        run_id: str,
        stage_name: str,
        attempt: int,
        file_name: str,
    ) -> Asset | None:
        return self.db.execute(
            select(Asset)
            .where(Asset.run_id == run_id)
            .where(Asset.stage_name == stage_name)
            .where(Asset.attempt == attempt)
            .where(Asset.file_name == file_name)
            .limit(1)
        ).scalar_one_or_none()

    def get_asset_by_abs_path(self, abs_path: str) -> Asset | None:
        normalized = str(abs_path or "").strip()
        if not normalized:
            return None
        return self.db.execute(
            select(Asset)
            .where(Asset.abs_path == normalized)
            .order_by(desc(Asset.created_at))
            .limit(1)
        ).scalar_one_or_none()

    def get_assets_by_abs_paths(self, paths: list[str]) -> dict[str, Asset]:
        normalized = [str(value or "").strip() for value in paths if str(value or "").strip()]
        if not normalized:
            return {}
        rows = list(
            self.db.execute(
                select(Asset)
                .where(Asset.abs_path.in_(normalized))
                .order_by(desc(Asset.created_at))
            ).scalars()
        )
        assets_by_path: dict[str, Asset] = {}
        for row in rows:
            key = str(row.abs_path or "").strip()
            if key and key not in assets_by_path:
                assets_by_path[key] = row
        return assets_by_path

    def add_score(
        self,
        *,
        run_id: str,
        stage_name: str,
        attempt: int,
        score_0_100: float,
        pass_fail: bool,
        rubric_json: dict[str, Any],
    ) -> Score:
        score = Score(
            run_id=run_id,
            stage_name=stage_name,
            attempt=attempt,
            score_0_100=score_0_100,
            pass_fail=pass_fail,
            rubric_json=_dumps(rubric_json),
        )
        self.db.add(score)
        self.db.commit()
        self.db.refresh(score)
        return score

    def run_details(self, run_id: str) -> tuple[Run | None, list[StageResult], list[Prompt], list[Asset], list[Score]]:
        run = self.get_run(run_id)
        if run is None:
            return None, [], [], [], []
        stages = list(
            self.db.execute(
                select(StageResult)
                .where(StageResult.run_id == run_id)
                .order_by(StageResult.created_at.asc())
            ).scalars()
        )
        prompts = list(
            self.db.execute(
                select(Prompt)
                .where(Prompt.run_id == run_id)
                .order_by(Prompt.created_at.asc())
            ).scalars()
        )
        assets = list(
            self.db.execute(
                select(Asset)
                .where(Asset.run_id == run_id)
                .order_by(Asset.created_at.asc())
            ).scalars()
        )
        scores = list(
            self.db.execute(
                select(Score)
                .where(Score.run_id == run_id)
                .order_by(Score.created_at.asc())
            ).scalars()
        )
        return run, stages, prompts, assets, scores

    def run_snapshot(self, run_id: str) -> tuple[Run | None, list[StageResult], list[Asset], list[Score]]:
        run = self.get_run(run_id)
        if run is None:
            return None, [], [], []
        stages = list(
            self.db.execute(
                select(StageResult)
                .where(StageResult.run_id == run_id)
                .order_by(StageResult.created_at.asc())
            ).scalars()
        )
        assets = list(
            self.db.execute(
                select(Asset)
                .where(Asset.run_id == run_id)
                .order_by(Asset.created_at.asc())
            ).scalars()
        )
        scores = list(
            self.db.execute(
                select(Score)
                .where(Score.run_id == run_id)
                .order_by(Score.created_at.asc())
            ).scalars()
        )
        return run, stages, assets, scores

    def get_asset(self, asset_id: str) -> Asset | None:
        return self.db.execute(select(Asset).where(Asset.id == asset_id)).scalar_one_or_none()

    def create_csv_job(
        self,
        *,
        batch_id: str,
        source_file_name: str,
        execution_mode: str,
        config_snapshot: dict[str, Any],
    ) -> CsvJob:
        job = CsvJob(
            batch_id=batch_id,
            source_file_name=source_file_name,
            execution_mode=execution_mode,
            config_snapshot_json=_dumps(config_snapshot),
            status="imported",
        )
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return self._release_instance(job)

    def get_csv_job(self, job_id: str) -> CsvJob | None:
        return self.db.execute(select(CsvJob).where(CsvJob.id == job_id)).scalar_one_or_none()

    def get_csv_job_by_batch(self, batch_id: str) -> CsvJob | None:
        return self.db.execute(select(CsvJob).where(CsvJob.batch_id == batch_id)).scalar_one_or_none()

    def list_csv_jobs(self) -> list[CsvJob]:
        return list(self.db.execute(select(CsvJob).order_by(desc(CsvJob.created_at))).scalars())

    def get_csv_job_row_counts(self, job_ids: list[str]) -> dict[str, int]:
        normalized = [str(value or "").strip() for value in job_ids if str(value or "").strip()]
        if not normalized:
            return {}
        rows = list(
            self.db.execute(
                select(CsvJobItem.csv_job_id, func.count(CsvJobItem.id))
                .where(CsvJobItem.csv_job_id.in_(normalized))
                .group_by(CsvJobItem.csv_job_id)
            )
        )
        return {str(job_id): int(count or 0) for job_id, count in rows}

    def update_csv_job(self, job: CsvJob, **updates: Any) -> CsvJob:
        managed = self._managed_instance(job)
        for key, value in updates.items():
            setattr(managed, key, value)
        self.db.add(managed)
        self.db.commit()
        self.db.refresh(managed)
        return self._release_instance(managed)

    def create_csv_job_item(
        self,
        *,
        csv_job_id: str,
        entry_id: str,
        row_index: int,
        source_row: dict[str, Any],
    ) -> CsvJobItem:
        item = CsvJobItem(
            csv_job_id=csv_job_id,
            entry_id=entry_id,
            row_index=row_index,
            source_row_json=_dumps(source_row),
            status="pending",
        )
        self.db.add(item)
        self.db.commit()
        self.db.refresh(item)
        return self._release_instance(item)

    def create_csv_job_item_uncommitted(
        self,
        *,
        csv_job_id: str,
        entry_id: str,
        row_index: int,
        source_row: dict[str, Any],
    ) -> CsvJobItem:
        item = CsvJobItem(
            csv_job_id=csv_job_id,
            entry_id=entry_id,
            row_index=row_index,
            source_row_json=_dumps(source_row),
            status="pending",
        )
        self.db.add(item)
        self.db.flush()
        return item

    def list_csv_job_items(self, csv_job_id: str) -> list[CsvJobItem]:
        return list(
            self.db.execute(
                select(CsvJobItem)
                .where(CsvJobItem.csv_job_id == csv_job_id)
                .order_by(CsvJobItem.row_index.asc())
            ).scalars()
        )

    def get_csv_job_item(self, item_id: str) -> CsvJobItem | None:
        return self.db.execute(select(CsvJobItem).where(CsvJobItem.id == item_id)).scalar_one_or_none()

    def update_csv_job_item(self, item: CsvJobItem, **updates: Any) -> CsvJobItem:
        managed = self._managed_instance(item)
        for key, value in updates.items():
            setattr(managed, key, value)
        self.db.add(managed)
        self.db.commit()
        self.db.refresh(managed)
        return self._release_instance(managed)

    def create_csv_task_node(
        self,
        *,
        csv_job_id: str,
        csv_job_item_id: str,
        step_name: str,
        task_key: str,
        profile_key: str,
        source_profile_key: str,
        branch_role: str,
        dependency_keys: list[str],
        dependency_task_ids: list[str],
        max_attempts: int = 2,
        status: str = "pending",
    ) -> CsvTaskNode:
        node = CsvTaskNode(
            csv_job_id=csv_job_id,
            csv_job_item_id=csv_job_item_id,
            step_name=step_name,
            task_key=task_key,
            profile_key=profile_key,
            source_profile_key=source_profile_key,
            branch_role=branch_role,
            dependency_keys_json=_dumps(dependency_keys),
            dependency_task_ids_json=_dumps(dependency_task_ids),
            max_attempts=max(1, int(max_attempts)),
            status=status,
        )
        self.db.add(node)
        self.db.commit()
        self.db.refresh(node)
        return self._release_instance(node)

    def create_csv_task_node_uncommitted(
        self,
        *,
        csv_job_id: str,
        csv_job_item_id: str,
        step_name: str,
        task_key: str,
        profile_key: str,
        source_profile_key: str,
        branch_role: str,
        dependency_keys: list[str],
        dependency_task_ids: list[str],
        max_attempts: int = 2,
        status: str = "pending",
    ) -> CsvTaskNode:
        node = CsvTaskNode(
            csv_job_id=csv_job_id,
            csv_job_item_id=csv_job_item_id,
            step_name=step_name,
            task_key=task_key,
            profile_key=profile_key,
            source_profile_key=source_profile_key,
            branch_role=branch_role,
            dependency_keys_json=_dumps(dependency_keys),
            dependency_task_ids_json=_dumps(dependency_task_ids),
            max_attempts=max(1, int(max_attempts)),
            status=status,
        )
        self.db.add(node)
        return node

    def list_csv_tasks(self, csv_job_id: str) -> list[CsvTaskNode]:
        return list(
            self.db.execute(
                select(CsvTaskNode)
                .where(CsvTaskNode.csv_job_id == csv_job_id)
                .order_by(CsvTaskNode.created_at.asc())
            ).scalars()
        )

    def get_csv_task(self, task_id: str) -> CsvTaskNode | None:
        return self.db.execute(select(CsvTaskNode).where(CsvTaskNode.id == task_id)).scalar_one_or_none()

    def update_csv_task(self, task: CsvTaskNode, **updates: Any) -> CsvTaskNode:
        managed = self._managed_instance(task)
        for key, value in updates.items():
            setattr(managed, key, value)
        self.db.add(managed)
        self.db.commit()
        self.db.refresh(managed)
        return self._release_instance(managed)

    def add_csv_task_attempt(
        self,
        *,
        csv_task_node_id: str,
        attempt_number: int,
        status: str,
        request_json: dict[str, Any],
        response_json: dict[str, Any],
        error_detail: str = "",
        finished_at: datetime | None = None,
    ) -> CsvTaskAttempt:
        record = CsvTaskAttempt(
            csv_task_node_id=csv_task_node_id,
            attempt_number=attempt_number,
            status=status,
            request_json=_dumps(request_json),
            response_json=_dumps(response_json),
            error_detail=error_detail,
            finished_at=finished_at,
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return self._release_instance(record)

    def list_csv_task_attempts(self, csv_task_node_id: str) -> list[CsvTaskAttempt]:
        return list(
            self.db.execute(
                select(CsvTaskAttempt)
                .where(CsvTaskAttempt.csv_task_node_id == csv_task_node_id)
                .order_by(CsvTaskAttempt.created_at.asc())
            ).scalars()
        )

    def claim_next_ready_csv_task(self) -> CsvTaskNode | None:
        queued = list(
            self.db.execute(
                select(CsvTaskNode)
                .join(CsvJob, CsvJob.id == CsvTaskNode.csv_job_id)
                .where(CsvTaskNode.status.in_(["queued", "pending"]))
                .where(CsvJob.status.in_(["queued", "running", "retry_queued"]))
                .order_by(CsvTaskNode.created_at.asc())
            ).scalars()
        )
        for task in queued:
            dependency_ids = [str(value) for value in _loads_list(task.dependency_task_ids_json) if str(value)]
            if dependency_ids:
                statuses = {
                    row.id: row.status
                    for row in self.db.execute(
                        select(CsvTaskNode.id, CsvTaskNode.status).where(CsvTaskNode.id.in_(dependency_ids))
                    )
                }
                if any(statuses.get(dep_id) != "completed" for dep_id in dependency_ids):
                    continue

            updated = self.db.execute(
                update(CsvTaskNode)
                .where(CsvTaskNode.id == task.id)
                .where(CsvTaskNode.status.in_(["queued", "pending"]))
                .values(status="running", started_at=datetime.utcnow())
            )
            if updated.rowcount == 0:
                self.db.rollback()
                continue
            self.db.commit()
            claimed = self.get_csv_task(task.id)
            if claimed is None:
                continue
            job = self.get_csv_job(claimed.csv_job_id)
            if job and job.started_at is None:
                self.update_csv_job(job, status="running", started_at=datetime.utcnow(), error_detail="")
            elif job and job.status in {"queued", "imported", "retry_queued"}:
                self.update_csv_job(job, status="running", error_detail="")
            item = self.get_csv_job_item(claimed.csv_job_item_id)
            if item is not None and item.status in {"pending", "queued"}:
                self.update_csv_job_item(item, status="running", error_detail="")
            return claimed
        return None

    def fail_stale_running_csv_tasks(self, *, timeout_seconds: int) -> list[str]:
        cutoff = datetime.utcnow().timestamp() - max(1, int(timeout_seconds))
        stale_tasks = list(
            self.db.execute(
                select(CsvTaskNode)
                .where(CsvTaskNode.status == "running")
                .where(CsvTaskNode.started_at.is_not(None))
            ).scalars()
        )
        timed_out_ids: list[str] = []
        affected_item_ids: set[str] = set()
        affected_job_ids: set[str] = set()
        for task in stale_tasks:
            if task.started_at is None or task.started_at.timestamp() > cutoff:
                continue
            task.status = "failed"
            task.error_summary = f"Timed out after {int(timeout_seconds)} seconds"
            task.finished_at = datetime.utcnow()
            self.db.add(task)
            timed_out_ids.append(task.id)
            affected_item_ids.add(task.csv_job_item_id)
            affected_job_ids.add(task.csv_job_id)
        if not timed_out_ids:
            return []
        self.db.commit()
        for item_id in affected_item_ids:
            item = self.get_csv_job_item(item_id)
            if item is None:
                continue
            tasks = [task for task in self.list_csv_tasks(item.csv_job_id) if task.csv_job_item_id == item.id]
            statuses = [task.status for task in tasks]
            if not statuses:
                item.status = "pending"
                item.error_detail = ""
            elif any(status == "running" for status in statuses):
                item.status = "running"
                item.error_detail = ""
            elif any(status == "failed" for status in statuses):
                first_failure = next((task for task in tasks if task.status == "failed"), None)
                item.status = "failed"
                item.error_detail = first_failure.error_summary if first_failure else "Task failed"
            elif any(status == "queued" for status in statuses):
                item.status = "running" if item.shadow_run_id or any(status in {"completed", "canceled"} for status in statuses) else "queued"
                item.error_detail = ""
            elif any(status == "pending" for status in statuses):
                item.status = "running" if item.shadow_run_id or any(status in {"completed", "canceled"} for status in statuses) else "pending"
                item.error_detail = ""
            elif any(status == "canceled" for status in statuses):
                item.status = "canceled"
                item.error_detail = "Canceled by user"
            else:
                item.status = "completed"
                item.error_detail = ""
            self.db.add(item)
        self.db.commit()
        for job_id in affected_job_ids:
            self.finalize_csv_job_status(job_id)
        return timed_out_ids

    def retry_failed_csv_tasks(self, csv_job_id: str) -> int:
        tasks = list(
            self.db.execute(
                select(CsvTaskNode)
                .where(CsvTaskNode.csv_job_id == csv_job_id)
                .where(CsvTaskNode.status == "failed")
            ).scalars()
        )
        count = 0
        for task in tasks:
            task.status = "queued"
            task.error_summary = ""
            task.finished_at = None
            self.db.add(task)
            count += 1
        if count:
            self.db.commit()
            job = self.get_csv_job(csv_job_id)
            if job is not None:
                self.update_csv_job(job, status="retry_queued", error_detail="", finished_at=None)
        return count

    def cancel_csv_job(self, csv_job_id: str) -> int:
        tasks = list(
            self.db.execute(
                select(CsvTaskNode)
                .where(CsvTaskNode.csv_job_id == csv_job_id)
                .where(CsvTaskNode.status.in_(["queued", "pending"]))
            ).scalars()
        )
        count = 0
        for task in tasks:
            task.status = "canceled"
            task.error_summary = "Canceled before execution"
            task.finished_at = datetime.utcnow()
            self.db.add(task)
            count += 1
        if count:
            self.db.commit()
        affected_items = {
            task.csv_job_item_id
            for task in tasks
            if str(task.csv_job_item_id or "").strip()
        }
        if affected_items:
            for item_id in affected_items:
                item = self.get_csv_job_item(item_id)
                if item is None:
                    continue
                item_tasks = [task for task in self.list_csv_tasks(csv_job_id) if task.csv_job_item_id == item.id]
                statuses = [task.status for task in item_tasks]
                if statuses and all(status == "canceled" for status in statuses):
                    item.status = "canceled"
                    item.error_detail = "Canceled by user"
                    self.db.add(item)
            self.db.commit()
        running = self.db.execute(
            select(func.count()).select_from(CsvTaskNode).where(CsvTaskNode.csv_job_id == csv_job_id).where(CsvTaskNode.status == "running")
        ).scalar_one()
        job = self.get_csv_job(csv_job_id)
        if job is not None:
            next_status = "cancel_requested" if running else "canceled"
            finished_at = None if running else datetime.utcnow()
            self.update_csv_job(job, status=next_status, finished_at=finished_at, error_detail="Canceled by user")
        return count

    def delete_csv_jobs(self, *, terminal_only: bool = True) -> int:
        stmt = select(CsvJob)
        if terminal_only:
            stmt = stmt.where(CsvJob.status.in_(["completed", "failed", "canceled", "cancel_requested"]))
        jobs = list(self.db.execute(stmt).scalars())
        count = 0
        for job in jobs:
            self.db.delete(job)
            count += 1
        if count:
            self.db.commit()
        return count

    def _queued_csv_task_is_blocked(self, task: CsvTaskNode) -> tuple[bool, str]:
        dependency_ids = [str(value) for value in _loads_list(task.dependency_task_ids_json) if str(value)]
        if not dependency_ids:
            return False, ""
        dependencies = list(
            self.db.execute(
                select(CsvTaskNode.id, CsvTaskNode.status, CsvTaskNode.step_name, CsvTaskNode.profile_key)
                .where(CsvTaskNode.id.in_(dependency_ids))
            )
        )
        if not dependencies:
            return True, "Missing dependency tasks"
        failed = [row for row in dependencies if row.status == "failed"]
        canceled = [row for row in dependencies if row.status == "canceled"]
        if failed:
            first = failed[0]
            return True, f"Blocked by failed dependency {first.step_name} {first.profile_key}".strip()
        if canceled:
            first = canceled[0]
            return True, f"Blocked by canceled dependency {first.step_name} {first.profile_key}".strip()
        return False, ""

    def finalize_csv_job_status(self, csv_job_id: str) -> CsvJob | None:
        job = self.get_csv_job(csv_job_id)
        if job is None:
            return None
        items = self.list_csv_job_items(csv_job_id)
        tasks = self.list_csv_tasks(csv_job_id)
        if items:
            item_statuses = [str(item.status or "").lower() for item in items]
            if all(status in {"completed", "failed", "canceled"} for status in item_statuses):
                if any(status == "failed" for status in item_statuses):
                    return self.update_csv_job(
                        job,
                        status="failed",
                        finished_at=datetime.utcnow(),
                        error_detail="One or more CSV DAG rows failed",
                    )
                if any(status == "canceled" for status in item_statuses):
                    return self.update_csv_job(
                        job,
                        status="canceled",
                        finished_at=datetime.utcnow(),
                        error_detail=job.error_detail or "Canceled by user",
                    )
                return self.update_csv_job(job, status="completed", finished_at=datetime.utcnow(), error_detail="")
        if not tasks:
            if not items:
                # Job was just created and the import hasn't committed yet — keep current status
                return job
            return self.update_csv_job(job, status="completed", finished_at=datetime.utcnow())
        statuses = [task.status for task in tasks]
        if any(status == "running" for status in statuses):
            if job.status == "cancel_requested":
                return self.update_csv_job(job, status="cancel_requested", error_detail=job.error_detail or "Canceled by user")
            return self.update_csv_job(job, status="running")
        if any(status == "pending" for status in statuses):
            if job.status == "cancel_requested":
                return self.update_csv_job(job, status="cancel_requested", error_detail=job.error_detail or "Canceled by user")
            if job.status in {"queued", "retry_queued"}:
                return self.update_csv_job(job, status=job.status)
            if job.started_at is not None or any(status in {"completed", "failed", "canceled", "queued"} for status in statuses):
                return self.update_csv_job(job, status="running")
            return self.update_csv_job(job, status="imported")
        if any(status == "queued" for status in statuses):
            queued_tasks = [task for task in tasks if task.status == "queued"]
            blocked_results = [self._queued_csv_task_is_blocked(task) for task in queued_tasks]
            if queued_tasks and all(blocked for blocked, _reason in blocked_results):
                blocked_reasons = [reason for blocked, reason in blocked_results if blocked and reason]
                if any("canceled dependency" in reason.lower() for reason in blocked_reasons) and not any(
                    "failed dependency" in reason.lower() for reason in blocked_reasons
                ):
                    return self.update_csv_job(
                        job,
                        status="canceled",
                        finished_at=datetime.utcnow(),
                        error_detail=blocked_reasons[0] if blocked_reasons else "Canceled by dependency chain",
                    )
                return self.update_csv_job(
                    job,
                    status="failed",
                    finished_at=datetime.utcnow(),
                    error_detail=blocked_reasons[0] if blocked_reasons else "Queued tasks are blocked by failed dependencies",
                )
            if job.status == "cancel_requested":
                next_status = "cancel_requested"
            elif job.started_at is not None or any(status in {"completed", "failed", "canceled"} for status in statuses):
                next_status = "running"
            else:
                next_status = "queued"
            return self.update_csv_job(job, status=next_status)
        if job.status == "cancel_requested" and any(status == "canceled" for status in statuses):
            return self.update_csv_job(job, status="canceled", finished_at=datetime.utcnow(), error_detail="Canceled by user")
        if all(status == "canceled" for status in statuses):
            return self.update_csv_job(job, status="canceled", finished_at=datetime.utcnow())
        if any(status == "failed" for status in statuses):
            finished_at = None if any(status in {"queued", "running"} for status in statuses) else datetime.utcnow()
            return self.update_csv_job(job, status="failed", finished_at=finished_at, error_detail="One or more CSV DAG tasks failed")
        return self.update_csv_job(job, status="completed", finished_at=datetime.utcnow(), error_detail="")

    def csv_job_overview(self, csv_job_id: str) -> dict[str, Any] | None:
        job = self.get_csv_job(csv_job_id)
        if job is None:
            return None
        items = self.list_csv_job_items(csv_job_id)
        tasks = self.list_csv_tasks(csv_job_id)
        step_counts: dict[str, dict[str, int]] = {}
        issues_by_step: dict[str, list[dict[str, Any]]] = {}
        for task in tasks:
            bucket = step_counts.setdefault(task.step_name, {})
            bucket[task.status] = bucket.get(task.status, 0) + 1
            if task.status == "failed":
                issues_by_step.setdefault(task.step_name, []).append(
                    {
                        "task_id": task.id,
                        "task_key": task.task_key,
                        "profile_key": task.profile_key,
                        "error": task.error_summary,
                    }
                )
        total_rows = len(items)
        started_at = job.started_at
        duration_seconds = 0.0
        if started_at:
            duration_end = job.finished_at or datetime.utcnow()
            duration_seconds = max(0.0, (duration_end - started_at).total_seconds())
        return {
            "job": job,
            "items": items,
            "tasks": tasks,
            "step_counts": step_counts,
            "issues_by_step": issues_by_step,
            "total_row_count": total_rows,
            "duration_seconds": duration_seconds,
        }

    def create_export(self, filter_json: dict[str, Any]) -> Export:
        record = Export(filter_json=_dumps(filter_json), status="pending")
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record

    def list_exports(self) -> list[Export]:
        return list(self.db.execute(select(Export).order_by(desc(Export.updated_at), desc(Export.created_at))).scalars())

    def update_export(self, export: Export, **updates: Any) -> Export:
        for key, value in updates.items():
            setattr(export, key, value)
        self.db.add(export)
        self.db.commit()
        self.db.refresh(export)
        return export

    def get_export(self, export_id: str) -> Export | None:
        return self.db.execute(select(Export).where(Export.id == export_id)).scalar_one_or_none()

    def list_runs_for_export(self, filters: dict[str, Any]) -> list[tuple[Run, Entry]]:
        stmt = select(Run, Entry).join(Entry, Entry.id == Run.entry_id)

        entry_ids = filters.get("entry_ids")
        run_ids = filters.get("run_ids")
        statuses = filters.get("status")
        min_score = filters.get("min_score")
        max_score = filters.get("max_score")

        if entry_ids:
            stmt = stmt.where(Run.entry_id.in_(entry_ids))
        if run_ids:
            stmt = stmt.where(Run.id.in_(run_ids))
        if statuses:
            stmt = stmt.where(Run.status.in_(statuses))
        if min_score is not None:
            stmt = stmt.where(Run.quality_score >= float(min_score))
        if max_score is not None:
            stmt = stmt.where(Run.quality_score <= float(max_score))
        stmt = stmt.where(Run.execution_mode == "legacy")

        stmt = stmt.order_by(Run.created_at.asc())
        return list(self.db.execute(stmt).all())

    @staticmethod
    def json_field_dict(value: str) -> dict[str, Any]:
        return _loads(value)

    def retry_run_from_last_failure(self, run: Run) -> Run:
        last_failed_stage = self.db.execute(
            select(StageResult)
            .where(StageResult.run_id == run.id)
            .where(StageResult.status.in_(["failed", "error", "timeout"]))
            .order_by(desc(StageResult.created_at))
            .limit(1)
        ).scalar_one_or_none()
        retry_stage = last_failed_stage.stage_name if last_failed_stage else "stage1_prompt"
        run.status = "retry_queued"
        run.retry_from_stage = retry_stage
        run.error_detail = ""
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def count_runs(self) -> int:
        return self.db.execute(select(func.count()).select_from(Run).where(Run.execution_mode == "legacy")).scalar_one()

    def create_shadow_run(
        self,
        *,
        entry_id: str,
        quality_threshold: int,
        max_optimization_attempts: int,
    ) -> Run:
        run = Run(
            entry_id=entry_id,
            execution_mode="csv_dag_shadow",
            status="queued",
            current_stage="queued",
            quality_threshold=max(MIN_QUALITY_THRESHOLD, int(quality_threshold)),
            max_optimization_attempts=max_optimization_attempts,
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return self._release_instance(run)
