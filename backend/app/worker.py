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


def _process_single_run(run_id: str) -> None:
    with SessionLocal() as db:
        runner = PipelineRunner(db)
        runner.process_run(run_id)


def _process_single_csv_task(task_id: str) -> None:
    with SessionLocal() as db:
        service = CsvDagService(db)
        service.execute_task(task_id)


def run_worker() -> None:
    settings = get_settings()
    configure_logging(settings.app_log_level)
    init_db()

    logger = logging.getLogger(__name__)
    logger.info("worker started")
    active_runs: dict[Future, str] = {}
    active_csv_tasks: dict[Future, str] = {}

    with ThreadPoolExecutor(max_workers=24) as executor:
        while True:
            with SessionLocal() as db:
                repo = Repository(db)
                config = repo.get_runtime_config()
                max_parallel_runs = max(1, min(int(config.max_parallel_runs), 12))
                max_variant_workers = max(1, min(int(config.max_variant_workers), 12))
                max_parallel_csv_tasks = max(1, min(max_parallel_runs * max_variant_workers, 24))
                poll_seconds = config.worker_poll_seconds or settings.worker_poll_seconds

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

            claimed_any = False
            while len(active_runs) < max_parallel_runs:
                with SessionLocal() as db:
                    repo = Repository(db)
                    run = repo.claim_next_queued_run()
                if run is None:
                    break
                claimed_any = True
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

            while len(active_csv_tasks) < max_parallel_csv_tasks:
                with SessionLocal() as db:
                    repo = Repository(db)
                    task = repo.claim_next_ready_csv_task()
                if task is None:
                    break
                claimed_any = True
                logger.info(
                    "csv task claimed",
                    extra={
                        "csv_task_id": task.id,
                        "task_key": task.task_key,
                        "active_csv_tasks": len(active_csv_tasks) + 1,
                        "max_parallel_csv_tasks": max_parallel_csv_tasks,
                    },
                )
                future = executor.submit(_process_single_csv_task, task.id)
                active_csv_tasks[future] = task.id

            if not claimed_any and not active_runs and not active_csv_tasks:
                time.sleep(poll_seconds)
            elif not claimed_any:
                time.sleep(0.25)


if __name__ == "__main__":
    run_worker()
