from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from time import monotonic
from typing import Any

from sqlalchemy import case, func, select, text
from sqlalchemy.orm import Session

from app.models import CsvTaskNode

logger = logging.getLogger(__name__)


class TaskHealthMonitor:
    """Observation-only, bounded task-health sampling for the existing worker."""

    def __init__(self, *, interval_seconds: int = 300, timeout_ms: int = 1000, stale_seconds: int = 420):
        self.interval_seconds = max(300, int(interval_seconds))
        self.timeout_ms = max(1, min(1000, int(timeout_ms)))
        self.stale_seconds = max(1, int(stale_seconds))
        self._next_run_at = 0.0
        self._lock = threading.Lock()

    def maybe_emit(self, db: Session, *, now_monotonic: float | None = None) -> dict[str, Any] | None:
        current = monotonic() if now_monotonic is None else now_monotonic
        if current < self._next_run_at or not self._lock.acquire(blocking=False):
            return None
        try:
            self._next_run_at = current + self.interval_seconds
            query_started = monotonic()
            summary = self._query(db)
            query_ms = round((monotonic() - query_started) * 1000, 1)
            if query_ms > self.timeout_ms:
                return None
            summary["query_ms"] = query_ms
            logger.info("csv task health", extra=summary)
            return summary
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.warning("csv task health skipped", extra={"status": type(exc).__name__})
            return None
        finally:
            self._lock.release()

    def _query(self, db: Session) -> dict[str, Any]:
        now = datetime.utcnow()
        stale_before = now - timedelta(seconds=self.stale_seconds)
        dialect = db.get_bind().dialect.name
        if dialect == "postgresql":
            row = db.execute(
                text(
                    """
                    WITH timeout_guard AS MATERIALIZED (
                      SELECT set_config('statement_timeout', :timeout_ms, true)
                    )
                    SELECT
                      count(*) FILTER (WHERE status IN ('pending', 'queued')) AS queued_tasks,
                      count(*) FILTER (WHERE status = 'running') AS running_tasks,
                      count(*) FILTER (WHERE status = 'failed') AS failed_tasks,
                      count(*) FILTER (
                        WHERE status = 'running' AND COALESCE(started_at, updated_at) < :stale_before
                      ) AS stale_tasks,
                      min(COALESCE(started_at, updated_at)) FILTER (WHERE status = 'running') AS oldest_running_at
                    FROM csv_task_nodes, timeout_guard
                    """
                ),
                {"timeout_ms": f"{self.timeout_ms}ms", "stale_before": stale_before},
            ).mappings().one()
        else:
            row = db.execute(
                select(
                    func.sum(case((CsvTaskNode.status.in_(["pending", "queued"]), 1), else_=0)).label("queued_tasks"),
                    func.sum(case((CsvTaskNode.status == "running", 1), else_=0)).label("running_tasks"),
                    func.sum(case((CsvTaskNode.status == "failed", 1), else_=0)).label("failed_tasks"),
                    func.sum(
                        case(
                            (
                                (CsvTaskNode.status == "running")
                                & (func.coalesce(CsvTaskNode.started_at, CsvTaskNode.updated_at) < stale_before),
                                1,
                            ),
                            else_=0,
                        )
                    ).label("stale_tasks"),
                    func.min(
                        case(
                            (CsvTaskNode.status == "running", func.coalesce(CsvTaskNode.started_at, CsvTaskNode.updated_at)),
                            else_=None,
                        )
                    ).label("oldest_running_at"),
                )
            ).mappings().one()
        oldest = row["oldest_running_at"]
        return {
            "queued_tasks": int(row["queued_tasks"] or 0),
            "running_tasks": int(row["running_tasks"] or 0),
            "failed_tasks": int(row["failed_tasks"] or 0),
            "stale_tasks": int(row["stale_tasks"] or 0),
            "oldest_running_age_seconds": max(0, int((now - oldest).total_seconds())) if oldest else None,
            "worker_heartbeat_age_seconds": None,
        }
