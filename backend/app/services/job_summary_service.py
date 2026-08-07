from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CsvJobAggregate, CsvTaskAttempt, CsvTaskNode
from app.services.cost_estimator import persisted_stage_cost_entries, summarize_run_cost_entries, summarize_run_costs
from app.services.repository import Repository

PRICING_VERSION = "provider-pricing-2026-07-22-v2"
TERMINAL_JOB_STATUSES = {"completed", "failed", "partial_failed", "canceled"}


def _seconds(started_at: datetime | None, finished_at: datetime | None) -> float | None:
    if started_at is None or finished_at is None:
        return None
    return max(0.0, (finished_at - started_at).total_seconds())


class JobSummaryService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = Repository(db)

    def get(self, job_id: str) -> dict[str, Any] | None:
        row = self.db.get(CsvJobAggregate, job_id)
        if row is None:
            return None
        return Repository.json_field_dict(row.summary_json)

    def get_details(self, job_id: str) -> dict[str, Any] | None:
        row = self.db.get(CsvJobAggregate, job_id)
        if row is None:
            return None
        return Repository.json_field_dict(row.details_json)

    def live_details(self, job_id: str) -> dict[str, Any] | None:
        """Return compact cost data without loading the large stage payload columns."""
        items = self.repo.list_csv_job_items(job_id)
        run_ids = list(dict.fromkeys(
            str(item.shadow_run_id) for item in items if str(item.shadow_run_id or "").strip()
        ))
        snapshots = self.repo.get_run_cost_inputs_by_ids(run_ids, include_stage_payloads=False)
        cost_entries: list[dict[str, Any]] = []
        for snapshot in snapshots.values():
            persisted_entries, cost_data_complete = persisted_stage_cost_entries(snapshot.get("stages", []))
            if not snapshot.get("cost_data_complete") or not cost_data_complete:
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
        return {
            "available": True,
            "is_final": False,
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
                "total_cost_usd": total_cost,
                "basis": "estimated" if cost_entries else "unavailable",
                "pricing_version": PRICING_VERSION if cost_entries else None,
                "cost_by_stage": {key: round(value, 6) for key, value in cost_by_stage.items()},
                "cost_by_provider": {key: round(value, 6) for key, value in cost_by_provider.items()},
                "cost_by_model": {key: round(value, 6) for key, value in cost_by_model.items()},
                "billable_calls": billable_calls if cost_entries else None,
                "retry_cost_usd": None,
                "failed_call_cost_usd": None,
                "unit_prices_used": [],
            },
            "slowest_stages": [],
            "slowest_words": [],
        }

    def finalize_if_terminal(self, job_id: str) -> dict[str, Any] | None:
        existing = self.db.get(CsvJobAggregate, job_id)
        if existing is not None and existing.is_final and existing.pricing_version == PRICING_VERSION:
            return Repository.json_field_dict(existing.summary_json)
        job = self.repo.get_csv_job(job_id)
        if job is None or str(job.status or "").lower() not in TERMINAL_JOB_STATUSES:
            return None

        items = self.repo.list_csv_job_items(job_id)
        tasks = self.repo.list_csv_tasks(job_id)
        tasks_by_item: dict[str, list[Any]] = defaultdict(list)
        for task in tasks:
            tasks_by_item[task.csv_job_item_id].append(task)

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

        task_durations: list[tuple[Any, float]] = []
        queue_wait_seconds = 0.0
        for task in tasks:
            duration = _seconds(task.started_at, task.finished_at)
            if duration is not None:
                task_durations.append((task, duration))
            wait = _seconds(task.created_at, task.started_at)
            if wait is not None:
                queue_wait_seconds += wait

        retry_attempts = list(
            self.db.execute(
                select(CsvTaskAttempt)
                .join(CsvTaskNode, CsvTaskNode.id == CsvTaskAttempt.csv_task_node_id)
                .where(CsvTaskAttempt.attempt_number > 1)
                .where(CsvTaskNode.csv_job_id == job_id)
            ).scalars()
        )
        retry_duration_seconds = sum(
            duration for attempt in retry_attempts
            if (duration := _seconds(attempt.created_at, attempt.finished_at)) is not None
        )

        run_ids = list(dict.fromkeys(
            str(item.shadow_run_id) for item in items if str(item.shadow_run_id or "").strip()
        ))
        snapshots = self.repo.get_run_cost_inputs_by_ids(run_ids)
        cost_entries: list[dict[str, Any]] = []
        stage_statuses: dict[tuple[str, int], str] = {}
        for snapshot in snapshots.values():
            for stage in snapshot.get("stages", []):
                stage_statuses[(str(stage.stage_name), int(stage.attempt or 0))] = str(stage.status or "")
            cost_entries.extend(summarize_run_costs(snapshot.get("stages", []), snapshot.get("assets", []))["stage_costs"])

        cost_by_stage: dict[str, float] = defaultdict(float)
        cost_by_provider: dict[str, float] = defaultdict(float)
        cost_by_model: dict[str, float] = defaultdict(float)
        unit_prices: dict[str, dict[str, Any]] = {}
        retry_cost = 0.0
        retry_billable_calls = 0
        failed_call_cost = 0.0
        billable_calls = 0
        for entry in cost_entries:
            cost = float(entry.get("estimated_cost_usd") or 0.0)
            stage_name = str(entry.get("stage_name") or "unknown")
            provider = str(entry.get("provider") or "unknown")
            model = str(entry.get("model") or "unknown")
            attempt = int(entry.get("attempt") or 0)
            units = max(1, int(entry.get("unit_count") or 1))
            cost_by_stage[stage_name] += cost
            cost_by_provider[provider] += cost
            cost_by_model[model] += cost
            if cost > 0:
                billable_calls += units
            if attempt > 1:
                retry_cost += cost
                retry_billable_calls += units
            parent_stage = "stage3_upgrade" if stage_name.startswith("stage3_") else stage_name
            if stage_statuses.get((parent_stage, attempt), "").lower() == "failed":
                failed_call_cost += cost
            if cost > 0:
                unit_prices[f"{provider}:{model}"] = {
                    "provider": provider,
                    "model": model,
                    "effective_unit_price_usd": round(cost / units, 8),
                    "estimate_basis": str(entry.get("estimate_basis") or ""),
                }

        total_cost = round(sum(float(entry.get("estimated_cost_usd") or 0.0) for entry in cost_entries), 6)
        elapsed = _seconds(job.started_at, job.finished_at)
        completed_count = len(completed_items)
        entries_by_id = self.repo.get_entries_by_ids([item.entry_id for item in items])
        item_processing: list[dict[str, Any]] = []
        for item in items:
            duration = sum(value for task, value in task_durations if task.csv_job_item_id == item.id)
            entry = entries_by_id.get(item.entry_id)
            item_processing.append({"word": entry.word if entry else "", "row_index": item.row_index, "processing_seconds": round(duration, 3)})
        stage_processing: dict[str, float] = defaultdict(float)
        for task, duration in task_durations:
            stage_processing[str(task.step_name or "unknown")] += duration

        summary = {
            "available": True,
            "is_final": True,
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
            "total_cost_usd": total_cost,
            "average_cost_per_completed_word_usd": round(total_cost / completed_count, 6) if completed_count else None,
            "cost_basis": "estimated" if cost_entries else "unavailable",
            "cost_label": "Estimated from recorded usage and image operations" if cost_entries else "Cost unavailable",
            "pricing_version": PRICING_VERSION if cost_entries else None,
            "billable_calls": billable_calls if cost_entries else None,
        }
        details = {
            "available": True,
            "is_final": True,
            "timing": {
                "wall_clock_seconds": summary["wall_clock_seconds"],
                "combined_processing_seconds": round(sum(value for _task, value in task_durations), 3),
                "queue_wait_seconds": round(queue_wait_seconds, 3),
                "provider_wait_seconds": None,
                "provider_wait_label": "Unavailable from existing timestamps",
                "retry_count": len(retry_attempts) + retry_billable_calls,
                "retry_duration_seconds": round(retry_duration_seconds, 3),
                "retry_duration_label": "Measured from recorded task retry timestamps; provider-internal retry duration unavailable",
            },
            "cost": {
                "total_cost_usd": total_cost,
                "basis": summary["cost_basis"],
                "pricing_version": summary["pricing_version"],
                "cost_by_stage": {key: round(value, 6) for key, value in cost_by_stage.items()},
                "cost_by_provider": {key: round(value, 6) for key, value in cost_by_provider.items()},
                "cost_by_model": {key: round(value, 6) for key, value in cost_by_model.items()},
                "billable_calls": summary["billable_calls"],
                "retry_cost_usd": round(retry_cost, 6),
                "failed_call_cost_usd": round(failed_call_cost, 6),
                "unit_prices_used": list(unit_prices.values()),
            },
            "slowest_stages": [
                {"stage": key, "combined_processing_seconds": round(value, 3)}
                for key, value in sorted(stage_processing.items(), key=lambda pair: pair[1], reverse=True)[:5]
            ],
            "slowest_words": sorted(item_processing, key=lambda row: row["processing_seconds"], reverse=True)[:5],
        }
        row = existing or CsvJobAggregate(csv_job_id=job_id)
        row.summary_json = json.dumps(summary, separators=(",", ":"), default=str)
        row.details_json = json.dumps(details, separators=(",", ":"), default=str)
        row.pricing_version = str(summary["pricing_version"] or "")
        row.cost_basis = str(summary["cost_basis"])
        row.is_final = True
        self.db.add(row)
        self.db.commit()
        return summary
