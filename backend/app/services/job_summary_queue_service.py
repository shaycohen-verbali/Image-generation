from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import CsvJobAggregate
from app.services.job_summary_service import PRICING_VERSION, JobSummaryService, TERMINAL_JOB_STATUSES
from app.services.repository import Repository

logger = logging.getLogger(__name__)


class JobSummaryQueueService:
    """Durable queue adapter for one bounded summary task per CSV job."""

    def __init__(self, db: Session | None):
        self.db = db
        self.repo = Repository(db) if db is not None else None

    def enqueue_if_terminal(self, job_id: str, force: bool = False) -> Any:
        settings = get_settings()
        if not settings.phase7_job_summary_enabled or not settings.job_summary_async_enabled:
            return None
        if self.repo is None:
            raise RuntimeError("a database session is required to enqueue a summary")
        job = self.repo.get_csv_job(job_id)
        if job is None or str(job.status or "").lower() not in TERMINAL_JOB_STATUSES:
            return None
        return self.repo.enqueue_csv_job_summary(
            job_id,
            target_pricing_version=PRICING_VERSION,
            max_attempts=settings.job_summary_max_attempts,
            force=force,
        )

    def claim(self, worker_id: str) -> Any:
        settings = get_settings()
        if not settings.phase7_job_summary_enabled or not settings.job_summary_async_enabled:
            return None
        if self.repo is None:
            raise RuntimeError("a database session is required to claim a summary")
        return self.repo.claim_next_csv_job_summary(worker_id)

    def recover_stale(self) -> int:
        settings = get_settings()
        if not settings.phase7_job_summary_enabled or not settings.job_summary_async_enabled:
            return 0
        if self.repo is None:
            raise RuntimeError("a database session is required to recover summaries")
        return self.repo.recover_stale_csv_job_summaries(settings.job_summary_stale_seconds)

    @staticmethod
    def _retry_delay(attempt_count: int) -> int:
        return 60 if int(attempt_count or 1) <= 1 else 300

    def process_claimed(self, job_id: str) -> bool:
        """Calculate outside the claim transaction using a fresh session."""
        job_id = str(job_id)
        started = time.monotonic()
        with SessionLocal() as db:
            try:
                task = Repository(db).get_csv_job_summary_task(job_id)
                if task is None:
                    raise RuntimeError("summary task no longer exists")
                attempt_count = int(task.attempt_count or 1)
                summary = JobSummaryService(db).finalize_if_terminal(job_id)
                if summary is None:
                    raise RuntimeError("CSV job is no longer terminal")
                aggregate = db.get(CsvJobAggregate, job_id)
                if aggregate is None or not aggregate.is_final or aggregate.pricing_version != PRICING_VERSION:
                    raise RuntimeError("final summary aggregate was not committed")
                Repository(db).mark_csv_job_summary_ready(job_id)
                logger.info(
                    "summary generation finished",
                    extra={
                        "job_id": job_id,
                        "attempt": int(attempt_count or 1),
                        "duration_seconds": round(time.monotonic() - started, 3),
                        "result": "ready",
                    },
                )
                return True
            except Exception as exc:  # noqa: BLE001 - queue retries are durable
                db.rollback()
                try:
                    Repository(db).retry_or_fail_csv_job_summary(
                        job_id,
                        error=f"{type(exc).__name__}: {exc}",
                        retry_delay_seconds=self._retry_delay(attempt_count),
                    )
                except Exception:  # noqa: BLE001 - preserve worker loop health
                    db.rollback()
                    logger.exception("summary task state update failed", extra={"job_id": job_id})
                logger.warning(
                    "summary generation failed",
                    extra={
                        "job_id": job_id,
                        "attempt": int(attempt_count or 1),
                        "duration_seconds": round(time.monotonic() - started, 3),
                        "result": "retry_or_failed",
                        "exception_class": type(exc).__name__,
                    },
                )
                return False
