from __future__ import annotations

import logging
import time

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.services.pipeline import PipelineRunner
from app.services.repository import Repository


def run_worker() -> None:
    settings = get_settings()
    configure_logging(settings.app_log_level)
    init_db()

    logger = logging.getLogger(__name__)
    logger.info("worker started")

    while True:
        with SessionLocal() as db:
            repo = Repository(db)
            config = repo.get_runtime_config()
            run = repo.claim_next_queued_run()
            if run is None:
                time.sleep(config.worker_poll_seconds or settings.worker_poll_seconds)
                continue

            logger.info("run claimed", extra={"run_id": run.id, "status": run.status})
            runner = PipelineRunner(db)
            runner.process_run(run.id)


if __name__ == "__main__":
    run_worker()
