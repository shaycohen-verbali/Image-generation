from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import json
import logging
import os
import signal
import socket
import threading
import time

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.init_db import init_db
from app.db.session import SessionLocal, engine
from app.services.csv_dag_service import CsvDagService
from app.services.pipeline import PipelineRunner
from app.services.repository import Repository
from app.services.task_health_monitor import TaskHealthMonitor
from app.services.job_summary_queue_service import JobSummaryQueueService
from app.models import CloudUploadBatch
from app.services.cloudflare_upload import CloudflareUploadService
from app.services.slack_alerts import SlackAlertService
from app.core.runtime import PROCESS_STARTED_AT
from app.services.storage import prune_runtime_cache

# Recover stale database tasks that were orphaned by a worker crash or restart.
# Tasks still owned by a live future must never be failed here: Python cannot
# cancel a running future, so releasing its slot would hide ongoing work and
# allow the process to exceed its configured parallelism.
CSV_TASK_TIMEOUT_SECONDS = 420
# The image pipeline is heavy on both providers and the database. Keep a
# reasonable upper bound so a burst of queued work does not create a second
# spike inside the process itself.
WORKER_CLAIM_BURST_MAX = 2
WORKER_CLAIM_SETTLE_SECONDS = 0.5
WORKER_ERROR_BACKOFF_MAX_SECONDS = 15.0
# Often enough that a dead worker is obvious within a minute, rare enough that
# an idle worker is not writing to the database constantly.
WORKER_HEARTBEAT_SECONDS = 30.0
SUMMARY_RECOVERY_SECONDS = 60.0
logger = logging.getLogger(__name__)

SHUTDOWN_REQUESTED = threading.Event()
_SHUTDOWN_STARTED_AT: float | None = None


def _effective_parallelism(requested_parallelism: int, hard_max_parallel: int) -> int:
    """Clamp the database/UI setting to the process memory safety ceiling."""
    requested = max(1, int(requested_parallelism or 1))
    hard_max = max(1, int(hard_max_parallel or 1))
    return min(requested, hard_max)


def _handle_shutdown_signal(signum: int, _frame) -> None:
    """Signal handler: set intent only; no database or network work here."""
    global _SHUTDOWN_STARTED_AT
    if SHUTDOWN_REQUESTED.is_set():
        return
    _SHUTDOWN_STARTED_AT = time.monotonic()
    SHUTDOWN_REQUESTED.set()
    logger.warning("worker shutdown requested", extra={"signal": signum})


def _install_shutdown_handlers() -> None:
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(signum, _handle_shutdown_signal)
        except ValueError:
            # Unit tests may invoke run_worker from a non-main thread. Render
            # always launches the real worker in the main thread.
            continue


def _shutdown_deadline(grace_seconds: int) -> float | None:
    if _SHUTDOWN_STARTED_AT is None:
        return None
    return _SHUTDOWN_STARTED_AT + max(1, int(grace_seconds))


def _claims_allowed(claiming_enabled: bool) -> bool:
    return bool(claiming_enabled and not SHUTDOWN_REQUESTED.is_set())


def _process_single_run(run_id: str) -> None:
    with SessionLocal() as db:
        runner = PipelineRunner(db)
        runner.process_run(run_id)


def _process_single_csv_task(task_id: str) -> None:
    with SessionLocal() as db:
        service = CsvDagService(db)
        task = service.execute_task(task_id)
        if get_settings().phase7_job_summary_enabled:
            try:
                JobSummaryQueueService(db).enqueue_if_terminal(task.csv_job_id)
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                logger.warning(
                    "job summary enqueue skipped",
                    extra={"status": type(exc).__name__},
                )
        # The batch may have just finished on this task. The alert claims itself
        # through the notification log, so calling it per task is safe.
        try:
            SlackAlertService(db).notify_job_finished(task.csv_job_id)
        except Exception as exc:  # noqa: BLE001 - never fail a task over a DM
            db.rollback()
            logger.warning("slack alert skipped", extra={"status": type(exc).__name__})


def _process_single_csv_summary(summary_task_id: str) -> None:
    # The claim session is already closed by the worker loop.  The queue
    # processor opens exactly one fresh session for calculation and state
    # updates, so no claim transaction remains open during the bounded scan.
    JobSummaryQueueService(None).process_claimed(summary_task_id)


def _process_cloud_upload_batch(batch_id: str) -> None:
    with SessionLocal() as db:
        batch = db.get(CloudUploadBatch, batch_id)
        if batch is None:
            return
        try:
            rows = json.loads(batch.source_rows_json or "[]")
        except (TypeError, ValueError) as exc:
            batch.status = "failed"
            batch.error_detail = f"Saved upload selection is invalid: {exc}"[:2000]
            db.commit()
            return
        if not isinstance(rows, list) or not rows:
            batch.status = "failed"
            batch.error_detail = "This upload batch has no saved source rows. Start a new upload."
            db.commit()
            return
        try:
            CloudflareUploadService(db).upload_rows(
                rows,
                bucket=batch.bucket,
                quality=batch.compression_quality,
                batch_id=batch.id,
            )
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            batch = db.get(CloudUploadBatch, batch_id)
            if batch is not None:
                batch.status = "failed"
                batch.error_detail = str(exc)[:2000]
                db.commit()
            raise


def _claim_next_cloud_upload_batch() -> str | None:
    with SessionLocal() as db:
        batch = db.scalars(
            select(CloudUploadBatch)
            .where(CloudUploadBatch.status.in_(("queued", "running")))
            .order_by(CloudUploadBatch.created_at.asc())
            .limit(1)
        ).first()
        if batch is None:
            return None
        batch.status = "running"
        db.commit()
        return batch.id


def _claim_budget(active_count: int, target_parallelism: int) -> int:
    available = max(0, int(target_parallelism) - int(active_count))
    if available <= 0:
        return 0
    return min(available, WORKER_CLAIM_BURST_MAX)


def run_worker() -> None:
    settings = get_settings()
    if settings.process_role not in {"worker", "all"}:
        raise RuntimeError(
            "PROCESS_ROLE=web cannot start the queue worker; run uvicorn app.main:app for the web service"
        )
    configure_logging(settings.app_log_level)
    init_db()
    _install_shutdown_handlers()
    try:
        prune_runtime_cache()
    except Exception as exc:  # noqa: BLE001 - cache cleanup is best effort
        logger.warning("runtime cache prune skipped", extra={"status": type(exc).__name__})

    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    claiming_enabled = bool(settings.worker_claiming_enabled)
    next_heartbeat_at = 0.0
    next_summary_recovery_at = 0.0
    startup_logged = False
    active_runs: dict[Future, str] = {}
    active_csv_tasks: dict[Future, str] = {}
    active_cloud_uploads: dict[Future, str] = {}
    active_summaries: dict[Future, str] = {}
    idle_poll_seconds = settings.worker_poll_seconds or 2.0
    error_backoff_seconds = idle_poll_seconds
    task_health_monitor = TaskHealthMonitor(
        interval_seconds=settings.phase7_monitoring_interval_seconds,
        timeout_ms=settings.phase7_monitoring_query_timeout_ms,
        stale_seconds=CSV_TASK_TIMEOUT_SECONDS,
    )

    # The executor itself is bounded by the environment ceiling.  A database
    # value can lower effective parallelism, but can never create more worker
    # futures than this process-level limit.
    executor = ThreadPoolExecutor(max_workers=settings.worker_hard_max_parallel)
    try:
        while True:
            try:
                timed_out_task_ids: list[str] = []
                with SessionLocal() as db:
                    repo = Repository(db)
                    config = repo.get_runtime_config()
                    requested_parallelism = max(1, int(config.max_parallel_runs or 1))
                    max_parallel_runs = _effective_parallelism(
                        requested_parallelism,
                        settings.worker_hard_max_parallel,
                    )
                    poll_seconds = config.worker_poll_seconds or settings.worker_poll_seconds
                    draining = SHUTDOWN_REQUESTED.is_set()
                    if claiming_enabled and not draining:
                        timed_out_task_ids = repo.fail_stale_running_csv_tasks(
                            timeout_seconds=CSV_TASK_TIMEOUT_SECONDS,
                            exclude_task_ids=active_csv_tasks.values(),
                        )
                    if time.monotonic() >= next_heartbeat_at:
                        repo.record_worker_heartbeat(
                            worker_id=worker_id, started_at=PROCESS_STARTED_AT
                        )
                        next_heartbeat_at = time.monotonic() + WORKER_HEARTBEAT_SECONDS
                    if settings.phase7_monitoring_enabled:
                        health = task_health_monitor.maybe_emit(db)
                        if health and int(health.get("stale_tasks") or 0):
                            try:
                                SlackAlertService(db).notify_stalled(
                                    stale_tasks=int(health["stale_tasks"]),
                                    oldest_age_seconds=health.get("oldest_running_age_seconds"),
                                )
                            except Exception as exc:  # noqa: BLE001
                                db.rollback()
                                logger.warning(
                                    "slack stall alert skipped",
                                    extra={"status": type(exc).__name__},
                                )
                    if (
                        claiming_enabled
                        and not draining
                        and settings.phase7_job_summary_enabled
                        and settings.job_summary_async_enabled
                        and time.monotonic() >= next_summary_recovery_at
                    ):
                        recovered_summaries = repo.recover_stale_csv_job_summaries(
                            settings.job_summary_stale_seconds
                        )
                        if recovered_summaries:
                            logger.warning(
                                "recovered stale summary tasks",
                                extra={"count": recovered_summaries},
                            )
                        next_summary_recovery_at = time.monotonic() + SUMMARY_RECOVERY_SECONDS

                    if not startup_logged:
                        logger.info(
                            "worker started",
                            extra={
                                "process_role": settings.process_role,
                                "requested_parallelism": requested_parallelism,
                                "hard_max_parallel": settings.worker_hard_max_parallel,
                                "effective_parallelism": max_parallel_runs,
                                "claiming_enabled": claiming_enabled,
                                "worker_id": worker_id,
                            },
                        )
                        startup_logged = True

                if timed_out_task_ids:
                    logger.warning(
                        "orphaned csv tasks timed out",
                        extra={
                            "csv_task_ids": timed_out_task_ids,
                            "timeout_seconds": CSV_TASK_TIMEOUT_SECONDS,
                        },
                    )

                done = [future for future in active_runs if future.done()]
                for future in done:
                    run_id = active_runs.pop(future)
                    try:
                        future.result()
                        logger.info("run finished", extra={"run_id": run_id})
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("run execution failed", extra={"run_id": run_id, "error": str(exc)})

                done_csv = [future for future in active_csv_tasks if future.done()]
                for future in done_csv:
                    task_id = active_csv_tasks.pop(future)
                    try:
                        future.result()
                        logger.info("csv task finished", extra={"csv_task_id": task_id})
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("csv task execution failed", extra={"csv_task_id": task_id, "error": str(exc)})

                done_uploads = [future for future in active_cloud_uploads if future.done()]
                for future in done_uploads:
                    batch_id = active_cloud_uploads.pop(future)
                    try:
                        future.result()
                        logger.info("cloud upload batch finished", extra={"cloud_upload_batch_id": batch_id})
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("cloud upload batch failed", extra={"cloud_upload_batch_id": batch_id, "error": str(exc)})

                done_summaries = [future for future in active_summaries if future.done()]
                for future in done_summaries:
                    summary_task_id = active_summaries.pop(future)
                    try:
                        future.result()
                        logger.info("csv summary finished", extra={"csv_job_id": summary_task_id})
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("csv summary failed", extra={"csv_job_id": summary_task_id, "error": str(exc)})

                active_total = len(active_runs) + len(active_csv_tasks) + len(active_cloud_uploads) + len(active_summaries)
                if SHUTDOWN_REQUESTED.is_set():
                    deadline = _shutdown_deadline(settings.worker_shutdown_grace_seconds)
                    if active_total == 0:
                        logger.info("worker drain complete")
                        break
                    if deadline is not None and time.monotonic() >= deadline:
                        unfinished = list(active_runs.values()) + list(active_csv_tasks.values()) + list(active_cloud_uploads.values()) + list(active_summaries.values())
                        logger.error(
                            "worker shutdown grace reached with unfinished work",
                            extra={"status": "grace_expired", "shutdown_grace_seconds": settings.worker_shutdown_grace_seconds},
                        )
                        logger.error(
                            "unfinished worker item ids",
                            extra={"unfinished_ids": [str(item) for item in unfinished]},
                        )
                        break
                    time.sleep(0.25)
                    continue

                if not _claims_allowed(claiming_enabled):
                    # A disabled worker is useful during cutover: it proves the
                    # process starts and heartbeats without touching queues.
                    time.sleep(poll_seconds)
                    continue

                claimed_count = 0
                # Cloudflare exports are user-triggered and should not wait
                # behind the normal CSV generation queue.
                if (
                    _claims_allowed(claiming_enabled)
                    and not active_cloud_uploads
                    and active_total < max_parallel_runs
                ):
                    batch_id = _claim_next_cloud_upload_batch()
                    if batch_id is not None:
                        claimed_count += 1
                        logger.info("cloud upload batch claimed", extra={"cloud_upload_batch_id": batch_id})
                        future = executor.submit(_process_cloud_upload_batch, batch_id)
                        active_cloud_uploads[future] = batch_id
                        active_total += 1

                for _ in range(_claim_budget(active_total, max_parallel_runs)):
                    if not _claims_allowed(claiming_enabled):
                        break
                    with SessionLocal() as db:
                        repo = Repository(db)
                        run = repo.claim_next_queued_run()
                    if run is None:
                        break
                    claimed_count += 1
                    logger.info(
                        "run claimed",
                        extra={
                            "run_id": run.id,
                            "status": run.status,
                            "active_runs": len(active_runs) + 1,
                            "max_parallel_runs": max_parallel_runs,
                        },
                    )
                    future = executor.submit(_process_single_run, run.id)
                    active_runs[future] = run.id
                    active_total += 1

                for _ in range(_claim_budget(active_total, max_parallel_runs)):
                    if not _claims_allowed(claiming_enabled):
                        break
                    with SessionLocal() as db:
                        repo = Repository(db)
                        task = repo.claim_next_ready_csv_task()
                    if task is None:
                        break
                    claimed_count += 1
                    logger.info(
                        "csv task claimed",
                        extra={
                            "csv_task_id": task.id,
                            "task_key": task.task_key,
                            "active_csv_tasks": len(active_csv_tasks) + 1,
                            "max_parallel_tasks": max_parallel_runs,
                        },
                    )
                    future = executor.submit(_process_single_csv_task, task.id)
                    active_csv_tasks[future] = task.id
                    active_total += 1

                # Summary calculation is intentionally a single, low-priority
                # unit of work.  The claim commits before the calculation and
                # consumes the same total capacity as every other job type.
                if (
                    settings.phase7_job_summary_enabled
                    and settings.job_summary_async_enabled
                    and _claims_allowed(claiming_enabled)
                    and not active_summaries
                    and active_total < max_parallel_runs
                ):
                    with SessionLocal() as db:
                        summary_task = JobSummaryQueueService(db).claim(worker_id)
                    if summary_task is not None:
                        claimed_count += 1
                        logger.info(
                            "csv summary claimed",
                            extra={
                                "csv_job_id": summary_task.csv_job_id,
                                "attempt": summary_task.attempt_count,
                            },
                        )
                        future = executor.submit(_process_single_csv_summary, summary_task.csv_job_id)
                        active_summaries[future] = summary_task.csv_job_id
                        active_total += 1

                error_backoff_seconds = idle_poll_seconds

                if claimed_count and active_total < max_parallel_runs:
                    time.sleep(WORKER_CLAIM_SETTLE_SECONDS)
                elif not claimed_count and not active_runs and not active_csv_tasks and not active_cloud_uploads and not active_summaries:
                    time.sleep(poll_seconds)
                elif not claimed_count:
                    time.sleep(0.25)
            except Exception as exc:  # noqa: BLE001
                logger.exception("worker loop iteration failed", extra={"error": str(exc)})
                time.sleep(error_backoff_seconds)
                error_backoff_seconds = min(
                    WORKER_ERROR_BACKOFF_MAX_SECONDS,
                    max(idle_poll_seconds, error_backoff_seconds * 2),
                )
    finally:
        # Do not release or mark running tasks successful when the local grace
        # window expires. Render may terminate the process, and the next worker
        # will use the existing stale-task recovery rules.
        has_active_work = bool(active_runs or active_csv_tasks or active_cloud_uploads or active_summaries)
        executor.shutdown(wait=not has_active_work, cancel_futures=False)
        if not has_active_work:
            try:
                engine.dispose()
            except Exception:  # pragma: no cover - best effort during process exit
                pass


if __name__ == "__main__":
    run_worker()
