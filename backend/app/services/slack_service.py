from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime
from typing import Any

import requests
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.services.csv_dag_service import CsvDagService
from app.services.repository import Repository

SLACK_SIGNATURE_VERSION = "v0"
SLACK_SIGNATURE_TOLERANCE_SECONDS = 60 * 5


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M UTC")


class SlackService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = Repository(db)
        self.csv_service = CsvDagService(db)
        self.settings = get_settings()

    @property
    def allowed_user_ids(self) -> set[str]:
        raw = str(self.settings.slack_allowed_user_ids or "").strip()
        return {item.strip() for item in raw.split(",") if item.strip()}

    def enabled(self) -> bool:
        return bool(str(self.settings.slack_signing_secret or "").strip() and str(self.settings.slack_bot_token or "").strip())

    def verify_signature(self, *, body: bytes, timestamp: str, signature: str) -> bool:
        secret = str(self.settings.slack_signing_secret or "").strip()
        if not secret or not timestamp or not signature:
            return False
        try:
            ts_value = int(timestamp)
        except ValueError:
            return False
        if abs(int(time.time()) - ts_value) > SLACK_SIGNATURE_TOLERANCE_SECONDS:
            return False
        basestring = f"{SLACK_SIGNATURE_VERSION}:{timestamp}:{body.decode('utf-8')}"
        digest = hmac.new(secret.encode("utf-8"), basestring.encode("utf-8"), hashlib.sha256).hexdigest()
        expected = f"{SLACK_SIGNATURE_VERSION}={digest}"
        return hmac.compare_digest(expected, signature)

    def authorized(self, user_id: str) -> bool:
        allowed = self.allowed_user_ids
        if not allowed:
            return True
        return str(user_id or "").strip() in allowed

    def slash_response(self, text: str, *, user_id: str, base_url: str) -> dict[str, str]:
        message = self._dispatch(text=text, user_id=user_id, base_url=base_url)
        return {"response_type": "ephemeral", "text": message}

    def dm_response_text(self, text: str, *, user_id: str, base_url: str) -> str:
        return self._dispatch(text=text, user_id=user_id, base_url=base_url)

    def post_message(self, *, channel: str, text: str) -> None:
        token = str(self.settings.slack_bot_token or "").strip()
        if not token:
            raise RuntimeError("Slack bot token not configured")
        response = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json={"channel": channel, "text": text},
            timeout=15,
        )
        payload = response.json()
        if response.status_code != 200 or not payload.get("ok"):
            raise RuntimeError(f"Slack postMessage failed: {payload}")

    def _dispatch(self, *, text: str, user_id: str, base_url: str) -> str:
        if not self.enabled():
            return "Slack integration is not configured on the backend yet."
        if not self.authorized(user_id):
            return "You are not allowed to use this Verbali Slack integration."

        normalized = str(text or "").strip()
        if not normalized:
            return self._help_text()

        parts = normalized.split()
        command = parts[0].lower()
        args = parts[1:]

        if command in {"help", "commands"}:
            return self._help_text()
        if command == "health":
            return self._health_summary()
        if command == "active":
            return self._active_summary()
        if command == "run":
            if not args:
                return "Usage: run <run_id>"
            return self._run_summary(args[0])
        if command == "csv":
            if not args:
                return "Usage: csv <csv_job_id>"
            return self._csv_summary(args[0])
        if command == "export":
            if not args:
                return "Usage: export <export_id|csv_job_id>"
            return self._export_summary(args[0], base_url=base_url)
        if command in {"retry", "stop", "start"}:
            return "Phase 1 is read-only in Slack. Use the web app for retry, stop, or start."
        if command == "status":
            if not args:
                return "Usage: status <run_id|csv_job_id|export_id>"
            return self._status_summary(args[0], base_url=base_url)

        return f"Unknown command: {command}\n\n{self._help_text()}"

    @staticmethod
    def _help_text() -> str:
        return (
            "*Verbali Slack commands*\n"
            "`health` - app and DB health\n"
            "`active` - active CSV jobs and legacy runs\n"
            "`run <run_id>` - run status\n"
            "`csv <csv_job_id>` - CSV job status\n"
            "`export <export_id|csv_job_id>` - export status\n"
            "`status <id>` - auto-detect run / csv job / export"
        )

    def _health_summary(self) -> str:
        run_count = self.repo.count_runs()
        active_csv_jobs = [
            job for job in self.csv_service.list_jobs()
            if str(job.get("status") or "") in {"imported", "queued", "retry_queued", "running", "cancel_requested"}
        ]
        active_runs = [
            run for run in self.repo.list_runs()
            if str(run.status or "") in {"queued", "retry_queued", "running", "cancel_requested"}
        ]
        return (
            "*Verbali health*\n"
            f"API: ok\n"
            f"DB: reachable\n"
            f"Legacy runs in DB: {run_count}\n"
            f"Active CSV jobs: {len(active_csv_jobs)}\n"
            f"Active legacy runs: {len(active_runs)}"
        )

    def _active_summary(self) -> str:
        csv_jobs = [
            job for job in self.csv_service.list_jobs()
            if str(job.get("status") or "") in {"imported", "queued", "retry_queued", "running", "cancel_requested"}
        ][:8]
        runs = [
            run for run in self.repo.list_runs()
            if str(run.status or "") in {"queued", "retry_queued", "running", "cancel_requested"}
        ][:8]

        lines = ["*Active work*"]
        if csv_jobs:
            lines.append("*CSV jobs*")
            for job in csv_jobs:
                lines.append(
                    f"- `{job['id']}` | {job.get('batch_id') or '-'} | {job.get('display_status') or job.get('status')}"
                    f" | rows={job.get('total_row_count') or 0}"
                )
        else:
            lines.append("No active CSV jobs.")

        if runs:
            lines.append("*Legacy runs*")
            entry_ids = [run.entry_id for run in runs if str(run.entry_id or "").strip()]
            entries = self.repo.get_entries_by_ids(entry_ids)
            for run in runs:
                entry = entries.get(run.entry_id)
                word = entry.word if entry is not None else "-"
                lines.append(
                    f"- `{run.id}` | {word} | {run.status} | {run.current_stage}"
                )
        else:
            lines.append("No active legacy runs.")

        return "\n".join(lines)

    def _run_summary(self, run_id: str) -> str:
        run = self.repo.get_run(run_id)
        if run is None:
            return f"Run not found: `{run_id}`"
        entry = self.repo.get_entry(run.entry_id)
        word = entry.word if entry is not None else "-"
        pos = entry.part_of_sentence if entry is not None else "-"
        category = entry.category if entry is not None else "-"
        score = "-" if run.quality_score is None else f"{float(run.quality_score):.0f}/{int(run.quality_threshold or 0)}"
        lines = [
            f"*Run* `{run.id}`",
            f"Word: {word}",
            f"POS: {pos}",
            f"Category: {category or '-'}",
            f"Status: {run.status}",
            f"Stage: {run.current_stage}",
            f"Score: {score}",
            f"Optimization attempt: {int(run.optimization_attempt or 0)}/{int(run.max_optimization_attempts or 0)}",
            f"Updated: {_format_dt(run.updated_at)}",
        ]
        if run.error_detail:
            lines.append(f"Error: {str(run.error_detail)[:300]}")
        return "\n".join(lines)

    def _csv_summary(self, job_id: str) -> str:
        job = self.csv_service.get_job(job_id)
        if job is None:
            return f"CSV job not found: `{job_id}`"
        profiles = ", ".join(job.get("requested_profiles") or []) or "-"
        lines = [
            f"*CSV job* `{job['id']}`",
            f"Batch: {job.get('batch_id') or '-'}",
            f"Status: {job.get('display_status') or job.get('status')}",
            f"Detail: {job.get('display_sub_status') or '-'}",
            f"Rows: {job.get('total_row_count') or 0}",
            f"Profiles: {profiles}",
            f"Started: {_format_dt(job.get('started_at'))}",
            f"Finished: {_format_dt(job.get('finished_at'))}",
        ]
        if job.get("error_detail"):
            lines.append(f"Error: {str(job['error_detail'])[:300]}")
        return "\n".join(lines)

    def _export_summary(self, export_id: str, *, base_url: str) -> str:
        normalized = str(export_id or "").strip()
        if normalized.startswith("csvjob_"):
            overview = self.csv_service.job_overview(normalized)
            if overview is None:
                return f"CSV job not found: `{normalized}`"
            job_payload = overview.get("job") or {}
            lines = [
                f"*CSV export* `{normalized}`",
                f"Job status: {job_payload.get('status') or '-'}",
                f"Export ready: {'yes' if overview.get('export_ready') else 'no'}",
            ]
            if overview.get("export_ready"):
                lines.append(f"Download: {base_url}/api/v1/csv-jobs/{normalized}/export/download")
            return "\n".join(lines)

        record = self.repo.get_export(normalized)
        if record is None:
            return f"Export not found: `{normalized}`"
        lines = [
            f"*Export* `{record.id}`",
            f"Status: {record.status}",
            f"Created: {_format_dt(record.created_at)}",
            f"Updated: {_format_dt(record.updated_at)}",
            f"Download package: {base_url}/api/v1/exports/{record.id}/download/package-zip",
        ]
        if record.error_detail:
            lines.append(f"Error: {str(record.error_detail)[:300]}")
        return "\n".join(lines)

    def _status_summary(self, identifier: str, *, base_url: str) -> str:
        value = str(identifier or "").strip()
        if value.startswith("run_"):
            return self._run_summary(value)
        if value.startswith("csvjob_"):
            return self._csv_summary(value)
        if value.startswith("exp_"):
            return self._export_summary(value, base_url=base_url)
        return "Use `run <run_id>`, `csv <csv_job_id>`, or `export <export_id|csv_job_id>`."
