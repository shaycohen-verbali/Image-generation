from datetime import datetime, timedelta
from types import SimpleNamespace

from app.models import CsvJobAggregate, CsvJobSummaryTask
from app.services import job_summary_queue_service
from app.services.job_summary_queue_service import JobSummaryQueueService
from app.services.repository import Repository


def _terminal_job(repo: Repository, batch_id: str):
    job = repo.create_csv_job(
        batch_id=batch_id,
        source_file_name="test.csv",
        execution_mode="csv_dag",
        config_snapshot={},
    )
    now = datetime.utcnow()
    return repo.update_csv_job(job, status="completed", started_at=now, finished_at=now)


def test_summary_queue_is_idempotent_and_single_claim(db_session) -> None:
    repo = Repository(db_session)
    job = _terminal_job(repo, "summary_queue_claim")

    first = repo.enqueue_csv_job_summary(job.id, target_pricing_version="pricing-v1", max_attempts=3)
    second = repo.enqueue_csv_job_summary(job.id, target_pricing_version="pricing-v1", max_attempts=3)
    assert first is not None
    assert second is not None
    assert second.status == "pending"
    assert second.attempt_count == 0

    claimed = repo.claim_next_csv_job_summary("worker-1")
    assert claimed is not None
    assert claimed.csv_job_id == job.id
    assert claimed.status == "running"
    assert claimed.attempt_count == 1
    assert repo.claim_next_csv_job_summary("worker-2") is None

    ready = repo.mark_csv_job_summary_ready(job.id)
    assert ready is not None
    assert ready.status == "ready"


def test_summary_queue_retries_then_marks_failed_after_attempt_limit(db_session) -> None:
    repo = Repository(db_session)
    job = _terminal_job(repo, "summary_queue_retry")
    repo.enqueue_csv_job_summary(job.id, target_pricing_version="pricing-v1", max_attempts=1)

    claimed = repo.claim_next_csv_job_summary("worker-1")
    assert claimed is not None
    failed = repo.retry_or_fail_csv_job_summary(job.id, error="out of memory", retry_delay_seconds=30)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.last_error == "out of memory"


def test_summary_queue_recovers_stale_claim_without_holding_calculation_lock(db_session) -> None:
    repo = Repository(db_session)
    job = _terminal_job(repo, "summary_queue_stale")
    repo.enqueue_csv_job_summary(job.id, target_pricing_version="pricing-v1", max_attempts=3)
    claimed = repo.claim_next_csv_job_summary("worker-1")
    assert claimed is not None

    row = db_session.get(CsvJobSummaryTask, job.id)
    assert row is not None
    row.started_at = datetime.utcnow() - timedelta(minutes=30)
    db_session.commit()

    assert repo.recover_stale_csv_job_summaries(stale_seconds=900) == 1
    recovered = db_session.get(CsvJobSummaryTask, job.id)
    assert recovered is not None
    assert recovered.status == "pending"
    assert recovered.worker_id is None


def test_current_aggregate_is_ready_and_pricing_change_requeues(db_session) -> None:
    repo = Repository(db_session)
    job = _terminal_job(repo, "summary_queue_pricing")
    db_session.add(
        CsvJobAggregate(
            csv_job_id=job.id,
            summary_json="{}",
            details_json="{}",
            pricing_version="pricing-v1",
            cost_basis="estimated",
            is_final=True,
        )
    )
    db_session.commit()

    ready = repo.enqueue_csv_job_summary(job.id, target_pricing_version="pricing-v1")
    assert ready is not None
    assert ready.status == "ready"
    refreshed = repo.enqueue_csv_job_summary(job.id, target_pricing_version="pricing-v2")
    assert refreshed is not None
    assert refreshed.status == "pending"
    assert refreshed.target_pricing_version == "pricing-v2"

    running = repo.claim_next_csv_job_summary("worker-1")
    assert running is not None
    unchanged = repo.enqueue_csv_job_summary(job.id, target_pricing_version="pricing-v2")
    assert unchanged is not None
    assert unchanged.status == "running"
    changed = repo.enqueue_csv_job_summary(job.id, target_pricing_version="pricing-v3")
    assert changed is not None
    assert changed.status == "pending"
    assert changed.attempt_count == 0
    claimed_again = repo.claim_next_csv_job_summary("worker-2")
    assert claimed_again is not None
    forced = repo.enqueue_csv_job_summary(job.id, target_pricing_version="pricing-v3", force=True)
    assert forced is not None
    assert forced.status == "pending"
    assert forced.attempt_count == 0


def test_queue_feature_flags_prevent_enqueue_and_claim(db_session, monkeypatch) -> None:
    repo = Repository(db_session)
    job = _terminal_job(repo, "summary_queue_flags")
    flags = SimpleNamespace(
        phase7_job_summary_enabled=True,
        job_summary_async_enabled=False,
        job_summary_max_attempts=3,
        job_summary_stale_seconds=900,
    )
    monkeypatch.setattr(job_summary_queue_service, "get_settings", lambda: flags)
    queue = JobSummaryQueueService(db_session)
    assert queue.enqueue_if_terminal(job.id) is None
    assert db_session.get(CsvJobSummaryTask, job.id) is None

    flags.job_summary_async_enabled = True
    queued = queue.enqueue_if_terminal(job.id)
    assert queued is not None
    assert queue.claim("worker-1") is not None
