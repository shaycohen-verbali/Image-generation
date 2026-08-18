from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import CsvJobAggregate, CsvTaskAttempt, CsvTaskNode
from app.services.cost_estimator import persisted_stage_cost_entries, summarize_run_cost_entries
from app.services.repository import Repository

PRICING_VERSION = "provider-pricing-2026-07-22-v2"
TERMINAL_JOB_STATUSES = {"completed", "failed", "partial_failed", "canceled"}
SUMMARY_GENERATION_STATUSES = {"pending", "running", "ready", "failed", "missing", "not_requested"}


def _seconds(started_at: datetime | None, finished_at: datetime | None) -> float | None:
    if started_at is None or finished_at is None:
        return None
    return max(0.0, (finished_at - started_at).total_seconds())


def _as_stage_value(stage: Any, key: str, default: Any = None) -> Any:
    if isinstance(stage, dict):
        return stage.get(key, default)
    return getattr(stage, key, default)


class JobSummaryService:
    """Read and build bounded, durable summaries for CSV jobs.

    The finalizer is intentionally separate from the HTTP read path.  It only
    reads compact, persisted cost rows and processes run IDs in bounded batches;
    it never needs the large provider payload columns.
    """

    def __init__(self, db: Session):
        self.db = db
        self.repo = Repository(db)

    def generation_status(self, job_id: str) -> dict[str, Any]:
        aggregate = self.db.get(CsvJobAggregate, job_id)
        if aggregate is not None and aggregate.is_final and aggregate.pricing_version == PRICING_VERSION:
            return {"status": "ready", "error": ""}
        try:
            task = self.repo.get_csv_job_summary_task(job_id)
        except SQLAlchemyError:
            # Keep the read path safe during a rolling deploy where the
            # additive queue migration has not reached this database yet.
            self.db.rollback()
            task = None
        if task is None:
            job = self.repo.get_csv_job(job_id)
            status = str(job.status or "").lower() if job is not None else ""
            return {
                "status": "missing" if status in TERMINAL_JOB_STATUSES else "not_requested",
                "error": "",
            }
        return {
            "status": str(task.status or "pending"),
            "error": str(task.last_error or "")[:1000],
        }

    @staticmethod
    def unavailable_details() -> dict[str, Any]:
        return {
            "available": False,
            "is_final": False,
            "generation_status": "unavailable",
            "timing": {
                "wall_clock_seconds": None,
                "combined_processing_seconds": None,
                "queue_wait_seconds": None,
                "provider_wait_seconds": None,
                "provider_wait_label": "Available after the final job summary is stored",
                "retry_count": None,
                "retry_duration_seconds": None,
                "retry_duration_label": "Available after the final job summary is stored",
            },
            "cost": {
                "total_cost_usd": None,
                "basis": "unavailable",
                "label": "Final summary cost is unavailable",
                "pricing_version": None,
                "cost_by_stage": {},
                "cost_by_provider": {},
                "cost_by_model": {},
                "billable_calls": None,
                "retry_cost_usd": None,
                "retry_billable_calls": None,
                "failed_call_cost_usd": None,
                "unit_prices_used": [],
            },
            "slowest_stages": [],
            "slowest_words": [],
        }

    def get(self, job_id: str) -> dict[str, Any] | None:
        row = self.db.get(CsvJobAggregate, job_id)
        if row is None:
            return None
        if not row.is_final or row.pricing_version != PRICING_VERSION:
            return None
        summary = Repository.json_field_dict(row.summary_json)
        if row.is_final and row.pricing_version == PRICING_VERSION:
            summary["generation_status"] = "ready"
            summary["generation_error"] = ""
        return summary

    def get_details(self, job_id: str) -> dict[str, Any] | None:
        row = self.db.get(CsvJobAggregate, job_id)
        if row is None:
            return None
        if not row.is_final or row.pricing_version != PRICING_VERSION:
            return None
        details = Repository.json_field_dict(row.details_json)
        if not details:
            return None
        return details

    def live_details(self, job_id: str) -> dict[str, Any] | None:
        """Return compact cost data for explicit, non-HTTP diagnostics only."""
        items = self.repo.list_csv_job_items(job_id)
        run_ids = list(dict.fromkeys(
            str(item.shadow_run_id) for item in items if str(item.shadow_run_id or "").strip()
        ))
        snapshots = self.repo.get_run_cost_inputs_by_ids(run_ids, include_stage_payloads=False)
        cost_entries: list[dict[str, Any]] = []
        complete = True
        for run_id in run_ids:
            snapshot = snapshots.get(run_id)
            if snapshot is None or not snapshot.get("cost_data_complete"):
                complete = False
                continue
            persisted_entries, cost_data_complete = persisted_stage_cost_entries(snapshot.get("stages", []))
            if not cost_data_complete:
                complete = False
                continue
            cost_entries.extend(
                summarize_run_cost_entries(
                    persisted_entries,
                    snapshot.get("assets", []),
                )["stage_costs"]
            )

        cost_by_stage: dict[str, float] = defaultdict(float)
        cost_by_provider: dict[str, float] = defaultdict(float)
        cost_by_model: dict[str, float] = defaultdict(float)
        billable_calls = 0
        for entry in cost_entries:
            cost = float(entry.get("estimated_cost_usd") or 0.0)
            cost_by_stage[str(entry.get("stage_name") or "unknown")] += cost
            cost_by_provider[str(entry.get("provider") or "unknown")] += cost
            cost_by_model[str(entry.get("model") or "unknown")] += cost
            if cost > 0:
                billable_calls += max(1, int(entry.get("unit_count") or 1))

        total_cost = round(sum(float(entry.get("estimated_cost_usd") or 0.0) for entry in cost_entries), 6)
        available = complete and bool(cost_entries)
        return {
            "available": True,
            "is_final": False,
            "generation_status": "diagnostic",
            "timing": {
                "wall_clock_seconds": None,
                "combined_processing_seconds": None,
                "queue_wait_seconds": None,
                "provider_wait_seconds": None,
                "provider_wait_label": "Available after the final job summary is stored",
                "retry_count": None,
                "retry_duration_seconds": None,
                "retry_duration_label": "Available after the final job summary is stored",
            },
            "cost": {
                "total_cost_usd": total_cost if available else None,
                "basis": "estimated" if available else "unavailable",
                "label": "Estimated from recorded usage" if available else "Legacy compact cost data is incomplete",
                "pricing_version": PRICING_VERSION if available else None,
                "cost_by_stage": {key: round(value, 6) for key, value in cost_by_stage.items()} if available else {},
                "cost_by_provider": {key: round(value, 6) for key, value in cost_by_provider.items()} if available else {},
                "cost_by_model": {key: round(value, 6) for key, value in cost_by_model.items()} if available else {},
                "billable_calls": billable_calls if available else None,
                "retry_cost_usd": None,
                "retry_billable_calls": None,
                "failed_call_cost_usd": None,
                "unit_prices_used": [],
            },
            "slowest_stages": [],
            "slowest_words": [],
        }

    def finalize_if_terminal(self, job_id: str) -> dict[str, Any] | None:
        settings = get_settings()
        existing = self.db.get(CsvJobAggregate, job_id)
        if existing is not None and existing.is_final and existing.pricing_version == PRICING_VERSION:
            summary = Repository.json_field_dict(existing.summary_json)
            summary["generation_status"] = "ready"
            summary["generation_error"] = ""
            return summary
        job = self.repo.get_csv_job(job_id)
        if job is None or str(job.status or "").lower() not in TERMINAL_JOB_STATUSES:
            return None

        items = self.repo.list_csv_job_items(job_id)
        tasks = self.repo.list_csv_tasks(job_id)
        tasks_by_item: dict[str, list[Any]] = defaultdict(list)
        item_processing_seconds: dict[str, float] = defaultdict(float)
        stage_processing_seconds: dict[str, float] = defaultdict(float)
        queue_wait_seconds = 0.0
        task_durations: list[tuple[Any, float]] = []
        for task in tasks:
            tasks_by_item[task.csv_job_item_id].append(task)
            duration = _seconds(task.started_at, task.finished_at)
            if duration is not None:
                task_durations.append((task, duration))
                item_processing_seconds[task.csv_job_item_id] += duration
                stage_processing_seconds[str(task.step_name or "unknown")] += duration
            wait = _seconds(task.created_at, task.started_at)
            if wait is not None:
                queue_wait_seconds += wait

        skipped_items = {
            item.id for item in items
            if str(item.status or "").lower() == "completed"
            and (
                not tasks_by_item.get(item.id)
                or "already exist" in str(item.error_detail or "").lower()
                or any("no person required" in str(task.error_summary or "").lower() for task in tasks_by_item[item.id])
            )
        }
        completed_items = [item for item in items if str(item.status or "").lower() == "completed"]
        failed_items = [item for item in items if str(item.status or "").lower() in {"failed", "canceled"}]

        retry_attempts = list(
            self.db.execute(
                select(
                    CsvTaskAttempt.id,
                    CsvTaskAttempt.attempt_number,
                    CsvTaskAttempt.created_at,
                    CsvTaskAttempt.finished_at,
                )
                .join(CsvTaskNode, CsvTaskNode.id == CsvTaskAttempt.csv_task_node_id)
                .where(CsvTaskAttempt.attempt_number > 1)
                .where(CsvTaskNode.csv_job_id == job_id)
            ).all()
        )
        retry_duration_seconds = sum(
            duration
            for attempt in retry_attempts
            if (duration := _seconds(attempt.created_at, attempt.finished_at)) is not None
        )

        run_ids = list(dict.fromkeys(
            str(item.shadow_run_id) for item in items if str(item.shadow_run_id or "").strip()
        ))
        batch_size = max(1, int(settings.job_summary_batch_size))
        cost_data_complete = True
        saw_cost_entries = False
        stage_statuses: dict[tuple[str, int], str] = {}
        cost_by_stage: dict[str, float] = defaultdict(float)
        cost_by_provider: dict[str, float] = defaultdict(float)
        cost_by_model: dict[str, float] = defaultdict(float)
        unit_prices: dict[str, dict[str, Any]] = {}
        retry_cost = 0.0
        retry_billable_calls = 0
        failed_call_cost = 0.0
        billable_calls = 0
        total_cost = 0.0

        for start in range(0, len(run_ids), batch_size):
            batch_run_ids = run_ids[start : start + batch_size]
            snapshots = self.repo.get_run_cost_inputs_by_ids(
                batch_run_ids,
                include_stage_payloads=False,
            )
            for run_id in batch_run_ids:
                snapshot = snapshots.get(run_id)
                if snapshot is None or not snapshot.get("cost_data_complete"):
                    cost_data_complete = False
                    continue
                stages = snapshot.get("stages", [])
                for stage in stages:
                    stage_statuses[(_as_stage_value(stage, "stage_name", ""), int(_as_stage_value(stage, "attempt", 0) or 0))] = str(
                        _as_stage_value(stage, "status", "") or ""
                    )
                persisted_entries, snapshot_complete = persisted_stage_cost_entries(stages)
                if not snapshot_complete:
                    cost_data_complete = False
                    continue
                expanded_entries = summarize_run_cost_entries(
                    persisted_entries,
                    snapshot.get("assets", []),
                )["stage_costs"]
                if expanded_entries:
                    saw_cost_entries = True
                for entry in expanded_entries:
                    cost = float(entry.get("estimated_cost_usd") or 0.0)
                    stage_name = str(entry.get("stage_name") or "unknown")
                    provider = str(entry.get("provider") or "unknown")
                    model = str(entry.get("model") or "unknown")
                    attempt = int(entry.get("attempt") or 0)
                    units = max(1, int(entry.get("unit_count") or 1))
                    cost_by_stage[stage_name] += cost
                    cost_by_provider[provider] += cost
                    cost_by_model[model] += cost
                    total_cost += cost
                    if cost > 0:
                        billable_calls += units
                    if attempt > 1:
                        retry_cost += cost
                        retry_billable_calls += units
                    status_stage_name = "stage3_upgrade" if stage_name.startswith("stage3_") else stage_name
                    if stage_statuses.get((status_stage_name, attempt), "").lower() == "failed":
                        failed_call_cost += cost
                    if cost > 0:
                        unit_prices[f"{provider}:{model}"] = {
                            "provider": provider,
                            "model": model,
                            "effective_unit_price_usd": round(cost / units, 8),
                            "estimate_basis": str(entry.get("estimate_basis") or ""),
                        }

        elapsed = _seconds(job.started_at, job.finished_at)
        completed_count = len(completed_items)
        entries_by_id = self.repo.get_entries_by_ids([item.entry_id for item in items])
        item_processing = [
            {
                "word": entries_by_id[item.entry_id].word if item.entry_id in entries_by_id else "",
                "row_index": item.row_index,
                "processing_seconds": round(item_processing_seconds.get(item.id, 0.0), 3),
            }
            for item in items
        ]

        cost_available = cost_data_complete and saw_cost_entries
        rounded_total = round(total_cost, 6) if cost_available else None
        cost_basis = "estimated" if cost_available else "unavailable"
        pricing_version = PRICING_VERSION if cost_available else None
        summary = {
            "available": True,
            "is_final": True,
            "generation_status": "ready",
            "generation_error": "",
            "status": str(job.status or ""),
            "counts": {
                "completed": completed_count,
                "failed": len(failed_items),
                "skipped": len(skipped_items),
                "queued": 0,
                "running": 0,
            },
            "wall_clock_seconds": round(elapsed, 3) if elapsed is not None else None,
            "average_elapsed_per_completed_word_seconds": round(elapsed / completed_count, 3) if elapsed is not None and completed_count else None,
            "total_cost_usd": rounded_total,
            "average_cost_per_completed_word_usd": round(total_cost / completed_count, 6) if cost_available and completed_count else None,
            "cost_basis": cost_basis,
            "cost_label": "Estimated from recorded usage and image operations" if cost_available else "Cost unavailable because compact usage data is incomplete",
            "pricing_version": pricing_version,
            "billable_calls": billable_calls if cost_available else None,
        }
        details = {
            "available": True,
            "is_final": True,
            "generation_status": "ready",
            "timing": {
                "wall_clock_seconds": summary["wall_clock_seconds"],
                "combined_processing_seconds": round(sum(value for _task, value in task_durations), 3),
                "queue_wait_seconds": round(queue_wait_seconds, 3),
                "provider_wait_seconds": None,
                "provider_wait_label": "Unavailable from existing timestamps",
                "retry_count": len(retry_attempts),
                "retry_duration_seconds": round(retry_duration_seconds, 3),
                "retry_duration_label": "Measured from recorded task retry timestamps; provider-internal retry duration unavailable",
            },
            "cost": {
                "total_cost_usd": rounded_total,
                "basis": cost_basis,
                "label": "Estimated from recorded usage and image operations" if cost_available else "Legacy compact cost data is incomplete",
                "pricing_version": pricing_version,
                "cost_by_stage": {key: round(value, 6) for key, value in cost_by_stage.items()} if cost_available else {},
                "cost_by_provider": {key: round(value, 6) for key, value in cost_by_provider.items()} if cost_available else {},
                "cost_by_model": {key: round(value, 6) for key, value in cost_by_model.items()} if cost_available else {},
                "billable_calls": billable_calls if cost_available else None,
                "retry_cost_usd": round(retry_cost, 6) if cost_available else None,
                "retry_billable_calls": retry_billable_calls if cost_available else None,
                "failed_call_cost_usd": round(failed_call_cost, 6) if cost_available else None,
                "unit_prices_used": list(unit_prices.values()) if cost_available else [],
            },
            "slowest_stages": [
                {"stage": key, "combined_processing_seconds": round(value, 3)}
                for key, value in sorted(stage_processing_seconds.items(), key=lambda pair: pair[1], reverse=True)[:5]
            ],
            "slowest_words": sorted(item_processing, key=lambda row: row["processing_seconds"], reverse=True)[:5],
        }
        row = existing or CsvJobAggregate(csv_job_id=job_id)
        row.summary_json = json.dumps(summary, separators=(",", ":"), default=str)
        row.details_json = json.dumps(details, separators=(",", ":"), default=str)
        row.pricing_version = PRICING_VERSION
        row.cost_basis = cost_basis
        row.is_final = True
        self.db.add(row)
        self.db.commit()
        return summary
