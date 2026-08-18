from __future__ import annotations

import hashlib
import hmac
import re
import time
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.runtime import PROCESS_STARTED_AT, format_duration, uptime_seconds
from app.services.csv_dag_service import CsvDagService
from app.services.http_client import get_http_session
from app.services.repository import Repository

SLACK_SIGNATURE_VERSION = "v0"
SLACK_SIGNATURE_TOLERANCE_SECONDS = 60 * 5

WRITE_COMMANDS = {"start", "stop", "cancel", "retry", "generate"}
WORD_SOURCE_TABLE = "word_inventory"
# The worker beats every 30s, so four missed beats is a real problem rather
# than a slow loop.
WORKER_HEARTBEAT_STALE_SECONDS = 120
# Above this many words, `generate` asks for confirmation before spending.
GENERATE_CONFIRM_THRESHOLD = 50
# "imported" means created but never started, which is not the same as working.
# Keeping the two apart stops `health` from reporting idle jobs as active.
RUNNING_JOB_STATUSES = {"queued", "retry_queued", "running", "cancel_requested"}
PENDING_JOB_STATUSES = {"imported"}
ACTIVE_JOB_STATUSES = RUNNING_JOB_STATUSES | PENDING_JOB_STATUSES
TERMINAL_JOB_STATUSES = {"completed", "failed", "partial_failed", "canceled"}


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M UTC")


def _format_age(value: datetime | None) -> str:
    if value is None:
        return "never"
    seconds = max(0, int((datetime.utcnow() - value).total_seconds()))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m ago"
    return f"{seconds // 86400}d ago"


class SlackService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = Repository(db)
        self.csv_service = CsvDagService(db)
        self.settings = get_settings()
        # One service instance handles one command, so listing jobs once per
        # instance is safe and keeps `health` from querying twice.
        self._jobs_cache: dict[frozenset[str], list[dict[str, Any]]] = {}

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

    def authorized(self, user_id: str, *, write: bool = False) -> bool:
        allowed = self.allowed_user_ids
        if not allowed:
            # Reads stay open for convenience. Writes must fail closed: an unset
            # SLACK_ALLOWED_USER_IDS would otherwise let anyone in the workspace
            # cancel a batch or spend provider credits.
            return not write
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
        response = get_http_session().post(
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

        normalized = str(text or "").strip()
        if not normalized:
            return self._help_text()

        parts = normalized.split()
        command = parts[0].lower()
        args = parts[1:]
        is_write = command in WRITE_COMMANDS

        if not self.authorized(user_id, write=is_write):
            if is_write and not self.allowed_user_ids:
                return (
                    "Write commands are disabled because SLACK_ALLOWED_USER_IDS is not set on "
                    "the backend. Set it to your Slack member ID and redeploy."
                )
            return "You are not allowed to use this Verbali Slack integration."

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
            return self._csv_summary(args[0] if args else "")
        if command == "export":
            if not args:
                return "Usage: export <export_id|csv_job_id>"
            return self._export_summary(args[0], base_url=base_url)
        if command == "start":
            return self._start_job(args[0] if args else "")
        if command in {"stop", "cancel"}:
            return self._stop_job(args[0] if args else "")
        if command == "retry":
            return self._retry_job(args[0] if args else "")
        if command in {"last", "latest"}:
            return self._last_job_summary()
        if command == "generate":
            return self._generate(args)
        if command == "status":
            # Bare `status` answers "what is happening right now", which is the
            # common case: you know a batch is running, not its id.
            if not args:
                return self._csv_summary("")
            return self._status_summary(args[0], base_url=base_url)

        return f"Unknown command: {command}\n\n{self._help_text()}"

    def _jobs_by_status(self, statuses: set[str]) -> list[dict[str, Any]]:
        key = frozenset(str(status or "").strip() for status in statuses if str(status or "").strip())
        if key not in self._jobs_cache:
            self._jobs_cache[key] = self.csv_service.list_jobs(statuses=set(key), limit=50)
        return self._jobs_cache[key]

    def _resolve_job_id(self, explicit: str, *, for_write: bool) -> tuple[str, str]:
        """Turn an omitted job id into the job the user almost certainly means.

        Returns (job_id, error_message); exactly one is ever non-empty.
        """
        candidate = str(explicit or "").strip()
        if candidate:
            return candidate, ""

        running = self._jobs_by_status(RUNNING_JOB_STATUSES)
        if len(running) == 1:
            return str(running[0]["id"]), ""
        if len(running) > 1:
            listed = "\n".join(
                f"- `{job['id']}` | {job.get('batch_id') or '-'}"
                f" | {job.get('display_status') or job.get('status')}"
                for job in running[:8]
            )
            return "", f"More than one job is running. Name the one you mean:\n{listed}"
        if for_write:
            return "", "No job is running right now. `active` shows what is queued or waiting."

        recent = self._jobs_by_status(TERMINAL_JOB_STATUSES)
        if recent:
            return str(recent[0]["id"]), ""
        return "", "No CSV jobs exist yet."

    def _start_job(self, job_id: str) -> str:
        candidate = str(job_id or "").strip()
        if not candidate:
            # Never guess which job to start - it spends provider credits.
            pending = self._jobs_by_status(PENDING_JOB_STATUSES)
            if not pending:
                return "No imported jobs are waiting to start."
            listed = "\n".join(
                f"- `{job['id']}` | {job.get('total_row_count') or 0} words"
                f" | created {_format_dt(job.get('created_at'))}"
                for job in pending[:8]
            )
            return f"Name the job to start:\n{listed}"

        job = self.repo.get_csv_job(candidate)
        if job is None:
            return f"CSV job not found: `{candidate}`"
        status = str(job.status or "").lower()
        if status in RUNNING_JOB_STATUSES:
            return f"`{candidate}` is already {status}."
        try:
            started = self.csv_service.start_job(candidate)
        except RuntimeError as exc:
            return f"Could not start `{candidate}`: {exc}"
        return f"Started `{started.id}` - status {started.status}."

    def _stop_job(self, job_id: str) -> str:
        resolved, error = self._resolve_job_id(job_id, for_write=True)
        if error:
            return error
        job = self.repo.get_csv_job(resolved)
        if job is None:
            return f"CSV job not found: `{resolved}`"
        if str(job.status or "").lower() in TERMINAL_JOB_STATUSES:
            return f"`{resolved}` already finished with status {job.status}."
        try:
            stopped, canceled = self.csv_service.cancel_job(resolved)
        except RuntimeError as exc:
            return f"Could not stop `{resolved}`: {exc}"
        return (
            f"Stop requested for `{stopped.id}` - {canceled} queued task(s) canceled,"
            f" status {stopped.status}.\n"
            "Images already generating will finish and still bill."
        )

    def _retry_job(self, job_id: str) -> str:
        # Retry normally targets the job that just failed, so falling back to the
        # most recent job is more useful than requiring one to be running.
        resolved, error = self._resolve_job_id(job_id, for_write=False)
        if error:
            return error
        if self.repo.get_csv_job(resolved) is None:
            return f"CSV job not found: `{resolved}`"
        try:
            retried, count = self.csv_service.retry_failures(resolved)
        except RuntimeError as exc:
            return f"Could not retry `{resolved}`: {exc}"
        if count == 0:
            return f"No failed tasks to retry on `{retried.id}` (status {retried.status})."
        return f"Requeued {count} failed task(s) on `{retried.id}` - status {retried.status}."

    @staticmethod
    def _help_text() -> str:
        return (
            "*Verbali Slack commands*\n"
            "`status` - what is running right now\n"
            "`last` - the most recently finished job\n"
            "`csv [csv_job_id]` - job detail, defaults to the running job\n"
            "`health` - API and worker uptime, DB, job counts\n"
            "`active` - jobs that are running or waiting to start\n"
            "`generate range 1-50` - generate from the Supabase word inventory\n"
            "`start [csv_job_id]` - start an imported job\n"
            "`stop [csv_job_id]` - stop the running job\n"
            "`retry [csv_job_id]` - requeue failed rows\n"
            "`run <run_id>` / `export <id>` - legacy run and export detail"
        )

    def _worker_line(self) -> str:
        beat = self.repo.get_latest_worker_heartbeat()
        if beat is None:
            return "Worker: unavailable - no heartbeat yet. The API and database remain available."
        age_seconds = max(0, int((datetime.utcnow() - beat.last_seen_at).total_seconds()))
        if age_seconds > WORKER_HEARTBEAT_STALE_SECONDS:
            return (
                f":rotating_light: Worker: NOT RESPONDING / unavailable - last beat {_format_age(beat.last_seen_at)}."
                " The API remains available; restart or redeploy the Render worker service."
            )
        uptime = int((datetime.utcnow() - beat.started_at).total_seconds())
        return (
            f"Worker: ok ({beat.id}) - last beat {_format_age(beat.last_seen_at)},"
            f" up {format_duration(uptime)} (restarted {_format_age(beat.started_at)})"
        )

    def _health_summary(self) -> str:
        run_count = self.repo.count_runs()
        running_jobs = self.repo.count_csv_jobs(statuses=RUNNING_JOB_STATUSES)
        pending_jobs = self.repo.count_csv_jobs(statuses=PENDING_JOB_STATUSES)
        active_runs = self.repo.count_runs(statuses=RUNNING_JOB_STATUSES)
        return (
            "*Verbali health*\n"
            f"API: ok - up {format_duration(uptime_seconds())}"
            f" (restarted {_format_age(PROCESS_STARTED_AT)})\n"
            "DB: reachable\n"
            f"{self._worker_line()}\n"
            f"Running CSV jobs: {running_jobs}\n"
            f"Imported, not started: {pending_jobs}\n"
            f"Active legacy runs: {active_runs}\n"
            f"Legacy runs in DB: {run_count}"
        )

    def _last_job_summary(self) -> str:
        finished = self._jobs_by_status(TERMINAL_JOB_STATUSES)
        if not finished:
            return "No job has finished yet."
        finished.sort(key=lambda job: job.get("finished_at") or datetime.min, reverse=True)
        return self._csv_summary(str(finished[0]["id"]))

    def _generate(self, args: list[str]) -> str:
        if not args:
            return (
                "*Generate images from the Supabase word inventory*\n"
                "`generate range 1-50` | `generate row <row_id>` | `generate all`\n"
                "Options: `pos=noun,verb` `gender=male,female` `age=kid` `skin=white`"
            )

        confirmed = any(token.lower() == "confirm" for token in args)
        options: dict[str, list[str]] = {}
        positional: list[str] = []
        for token in args:
            if token.lower() == "confirm":
                continue
            if "=" in token:
                key, _, value = token.partition("=")
                options[key.strip().lower()] = [
                    item.strip().lower() for item in value.split(",") if item.strip()
                ]
            else:
                positional.append(token)

        mode = positional[0].lower() if positional else ""
        row_id, range_start, range_end = "", None, None
        if mode == "range":
            spec = positional[1] if len(positional) > 1 else ""
            match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", spec)
            if match is None:
                return "Usage: `generate range 1-50`"
            range_start, range_end = int(match.group(1)), int(match.group(2))
            if range_end < range_start:
                return "The range end cannot be smaller than the range start."
            selection_mode = "range"
        elif mode == "row":
            row_id = positional[1] if len(positional) > 1 else ""
            if not row_id:
                return "Usage: `generate row <row_id>`"
            selection_mode = "single"
        elif mode == "all":
            selection_mode = "all"
        else:
            return f"Unknown selection `{mode or '(none)'}`. Use `range`, `row`, or `all`."

        from app.services.word_sources import WordSourceService

        try:
            rows = WordSourceService().get_rows(
                WORD_SOURCE_TABLE,
                selection_mode=selection_mode,
                row_id=row_id,
                range_start=range_start,
                range_end=range_end,
                parts_of_speech=options.get("pos", []),
            )
        except ValueError as exc:
            return f"Bad selection: {exc}"
        except RuntimeError as exc:
            return f"Word source unavailable: {exc}"
        if not rows:
            return "No rows in `word_inventory` matched that selection."

        if len(rows) > GENERATE_CONFIRM_THRESHOLD and not confirmed:
            # A typo here can cost real money, so make the large case deliberate.
            return (
                f"That selection matches *{len(rows)} words* and will spend provider credits.\n"
                "Send the same command again with `confirm` on the end to start it."
            )

        result = self.csv_service.import_word_source_rows(
            table_name=WORD_SOURCE_TABLE,
            rows=rows,
            person_gender_options=options.get("gender", []),
            person_age_options=options.get("age", []),
            person_skin_color_options=options.get("skin", []),
        )
        job_id = str(result.get("job_id") or "")
        try:
            started = self.csv_service.start_job(job_id)
        except RuntimeError as exc:
            return f"Imported `{job_id}` but could not start it: {exc}"

        lines = [
            f"Started `{started.id}` - status {started.status}",
            f"Imported {result.get('imported_count') or 0} words"
            f" (skipped {result.get('skipped_count') or 0})",
        ]
        profiles = ", ".join(
            options.get("gender", []) + options.get("age", []) + options.get("skin", [])
        )
        if profiles:
            lines.append(f"Variants: {profiles}")
        lines.append("Reply `status` to watch it.")
        return "\n".join(lines)

    def _active_summary(self) -> str:
        csv_jobs = self._jobs_by_status(ACTIVE_JOB_STATUSES)[:8]
        runs = self.repo.list_runs(
            statuses={"queued", "retry_queued", "running", "cancel_requested"},
            limit=8,
        )

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
        requested = str(job_id or "").strip()
        resolved, error = self._resolve_job_id(requested, for_write=False)
        if error:
            return error
        summary = self.csv_service.job_summary(resolved)
        if summary is None:
            return f"CSV job not found: `{resolved}`"

        job = summary["job"]
        counts = summary["word_counts"]
        status = str(job.get("status") or "")
        total = int(job.get("total_row_count") or 0)
        done = int(counts.get("completed") or 0)

        lines: list[str] = []
        if not requested and status.lower() in TERMINAL_JOB_STATUSES:
            lines.append("_Nothing is running. Showing the most recent job._")

        profiles = ", ".join(job.get("requested_profiles") or []) or "-"
        lines += [
            f"*CSV job* `{job['id']}`",
            f"Batch: {job.get('batch_id') or '-'}",
            f"Status: {job.get('display_status') or status} - {job.get('display_sub_status') or '-'}",
            f"Requested: {total} words | {profiles} | {job.get('source_file_name') or '-'}",
            f"Progress: {done} done | {counts.get('failure') or 0} failed"
            f" | {counts.get('running') or 0} running | {counts.get('pending') or 0} pending"
            + (f" | {round(done * 100 / total)}%" if total else ""),
        ]

        last_image_at = self.repo.get_csv_job_last_completed_task_at(job["id"])
        lines.append(f"Last image: {_format_age(last_image_at)} ({_format_dt(last_image_at)})")
        if summary.get("is_stale"):
            stale_minutes = int(summary.get("stale_seconds") or 0) // 60
            lines.append(f":warning: No progress for {stale_minutes}m - this job may be stuck.")

        cost = (summary.get("job_summary") or {}).get("total_cost_usd")
        if cost is not None:
            lines.append(f"Cost so far: ${float(cost):.2f}")

        lines.append(f"Started: {_format_dt(job.get('started_at'))}")
        if job.get("finished_at"):
            lines.append(f"Finished: {_format_dt(job.get('finished_at'))}")
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
