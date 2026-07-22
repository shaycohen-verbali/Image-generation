from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import logging
import time

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.services.csv_dag_service import CsvDagService
from app.services.pipeline import PipelineRunner
from app.services.repository import Repository
from app.services.task_health_monitor import TaskHealthMonitor
from app.services.job_summary_service import JobSummaryService

# Recover stale database tasks that were orphaned by a worker crash or restart.
# Tasks still owned by a live future must never be failed here: Python cannot
# cancel a running future, so releasing its slot would hide ongoing work and
# allow the process to exceed its configured parallelism.
CSV_TASK_TIMEOUT_SECONDS = 420
# The image pipeline is heavy on both providers and the database. Keep a
# reasonable upper bound so a burst of queued work does not create a second
# spike inside the process itself.
WORKER_EXECUTOR_MAX = 64
WORKER_CLAIM_BURST_MAX = 2
WORKER_CLAIM_SETTLE_SECONDS = 0.5
WORKER_ERROR_BACKOFF_MAX_SECONDS = 15.0
logger = logging.getLogger(__name__)


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
                JobSummaryService(db).finalize_if_terminal(task.csv_job_id)
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                logger.warning(
                    "job summary skipped",
                    extra={"status": type(exc).__name__},
                )


def _claim_budget(active_count: int, target_parallelism: int) -> int:
    available = max(0, int(target_parallelism) - int(active_count))
    if available <= 0:
        return 0
    return min(available, WORKER_CLAIM_BURST_MAX)


def run_worker() -> None:
    settings = get_settings()
    configure_logging(settings.app_log_level)
    init_db()

    logger.info("worker started")
    active_runs: dict[Future, str] = {}
    active_csv_tasks: dict[Future, str] = {}
    idle_poll_seconds = settings.worker_poll_seconds or 2.0
    error_backoff_seconds = idle_poll_seconds
    task_health_monitor = TaskHealthMonitor(
        interval_seconds=settings.phase7_monitoring_interval_seconds,
        timeout_ms=settings.phase7_monitoring_query_timeout_ms,
        stale_seconds=CSV_TASK_TIMEOUT_SECONDS,
    )

    with ThreadPoolExecutor(max_workers=WORKER_EXECUTOR_MAX) as executor:
        while True:
            try:
                with SessionLocal() as db:
                    repo = Repository(db)
                    config = repo.get_runtime_config()
                    max_parallel_runs = max(1, int(config.max_parallel_runs or 1))
                    poll_seconds = config.worker_poll_seconds or settings.worker_poll_seconds
                    timed_out_task_ids = repo.fail_stale_running_csv_tasks(
                        timeout_seconds=CSV_TASK_TIMEOUT_SECONDS,
                        exclude_task_ids=active_csv_tasks.values(),
                    )
                    if settings.phase7_monitoring_enabled:
                        task_health_monitor.maybe_emit(db)

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

                claimed_count = 0
                active_total = len(active_runs) + len(active_csv_tasks)
                for _ in range(_claim_budget(active_total, max_parallel_runs)):
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

                error_backoff_seconds = idle_poll_seconds

                if claimed_count and active_total < max_parallel_runs:
                    time.sleep(WORKER_CLAIM_SETTLE_SECONDS)
                elif not claimed_count and not active_runs and not active_csv_tasks:
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


if __name__ == "__main__":
    run_worker()
