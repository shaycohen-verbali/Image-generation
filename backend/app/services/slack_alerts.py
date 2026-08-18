"""Unsolicited Slack messages: batch finished, batch stalled.

Alerts are sent from the worker process, which has no request context, so this
module never assumes one. Every alert is claimed through the notification log
first, because the worker restarts and would otherwise re-announce old jobs.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.services.repository import Repository

logger = logging.getLogger(__name__)

JOB_FINISHED_KIND = "csv_job_finished"
JOB_STALLED_KIND = "csv_job_stalled"
TERMINAL_JOB_STATUSES = {"completed", "failed", "partial_failed", "canceled"}

_STATUS_ICON = {
    "completed": ":white_check_mark:",
    "partial_failed": ":warning:",
    "failed": ":x:",
    "canceled": ":black_square_for_stop:",
}


class SlackAlertService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = Repository(db)
        self.settings = get_settings()

    def enabled(self) -> bool:
        return bool(
            str(self.settings.slack_bot_token or "").strip()
            and str(self.settings.slack_alert_user_id or "").strip()
        )

    def _send(self, text: str) -> None:
        # Imported here so the worker does not pull the whole Slack command
        # surface (and its CsvDagService) on module import.
        from app.services.slack_service import SlackService

        SlackService(self.db).post_message(
            channel=str(self.settings.slack_alert_user_id or "").strip(),
            text=text,
        )

    def notify_job_finished(self, job_id: str) -> bool:
        """Announce a terminal job exactly once. Returns True if a DM was sent."""
        if not self.enabled():
            return False
        job = self.repo.get_csv_job(job_id)
        if job is None or str(job.status or "").lower() not in TERMINAL_JOB_STATUSES:
            return False
        if not self.repo.claim_notification(kind=JOB_FINISHED_KIND, subject_id=job_id):
            return False

        try:
            from app.services.csv_dag_service import CsvDagService

            summary = CsvDagService(self.db).job_summary(job_id) or {}
            counts = summary.get("word_counts") or {}
            serialized = summary.get("job") or {}
            total = int(serialized.get("total_row_count") or 0)
            cost = (summary.get("job_summary") or {}).get("total_cost_usd")

            icon = _STATUS_ICON.get(str(job.status or "").lower(), ":information_source:")
            lines = [
                f"{icon} *Batch finished* `{job.id}`",
                f"Batch: {job.batch_id}",
                f"Status: {job.status}",
                f"Result: {counts.get('completed') or 0} done"
                f" | {counts.get('failure') or 0} failed"
                f" | {total} requested",
            ]
            if cost is not None:
                lines.append(f"Cost: ${float(cost):.2f}")
            if int(counts.get("failure") or 0):
                lines.append(f"Reply `retry {job.id}` to requeue the failed rows.")
            self._send("\n".join(lines))
            return True
        except Exception as exc:  # noqa: BLE001 - a failed alert must not fail the job
            logger.warning("slack job alert failed", extra={"status": type(exc).__name__})
            return False

    def notify_stalled(self, *, stale_tasks: int, oldest_age_seconds: int | None) -> bool:
        """Warn once per stall that tasks are running but not progressing."""
        if not self.enabled() or stale_tasks <= 0:
            return False
        running = self.repo.list_csv_jobs(
            statuses={"queued", "retry_queued", "running"},
            limit=50,
        )
        if not running:
            return False
        # Oldest first: with several jobs running, the long-lived one is the one
        # stalling. Sorting also keeps the chosen job stable across ticks.
        running.sort(key=lambda job: job.started_at or datetime.max)
        job_id = running[0].id
        # Once per job per stall depth, so a monitor tick every 5 minutes does
        # not nag, but a worsening stall still says so.
        if not self.repo.claim_notification(
            kind=JOB_STALLED_KIND, subject_id=f"{job_id}:{stale_tasks}"
        ):
            return False
        age = f"{oldest_age_seconds // 60}m" if oldest_age_seconds else "a while"
        try:
            self._send(
                f":warning: *Batch may be stuck* `{job_id}`\n"
                f"{stale_tasks} task(s) have been running for {age} without finishing.\n"
                f"Reply `status` for detail, or `stop {job_id}`."
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("slack stall alert failed", extra={"status": type(exc).__name__})
            return False
