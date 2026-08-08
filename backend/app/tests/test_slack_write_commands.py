from __future__ import annotations

from datetime import datetime, timedelta

from app.core.config import get_settings
from app.models import CsvJob, CsvJobItem, CsvTaskNode, Entry
from app.services.slack_service import SlackService


def _configure(*, allowed: str) -> None:
    settings = get_settings()
    settings.slack_signing_secret = "test-signing-secret"
    settings.slack_bot_token = "xoxb-test-token"
    settings.slack_allowed_user_ids = allowed


def _make_job(
    db_session,
    *,
    job_id: str,
    status: str,
    batch: str,
    rows: int = 2,
    pending_rows: int = 0,
) -> CsvJob:
    """One completed row, one failed row, and optionally rows still queued.

    `pending_rows` matters for cancellation: with nothing left queued, a stop
    request finalizes the job immediately instead of cancelling anything.
    """
    job = CsvJob(id=job_id, batch_id=batch, status=status, source_file_name="supabase:word_inventory")
    if status != "imported":
        job.started_at = datetime.utcnow() - timedelta(minutes=30)
    db_session.add(job)
    db_session.flush()
    for index in range(rows + pending_rows):
        if index >= rows:
            row_status, task_status, finished = "pending", "queued", None
        elif index == 0:
            row_status, task_status = "completed", "completed"
            finished = datetime.utcnow() - timedelta(minutes=5)
        else:
            row_status, task_status, finished = "failed", "failed", None
        entry = Entry(
            id=f"ent_{job_id}_{index}",
            word=f"{job_id}_word{index}",
            part_of_sentence="noun",
            category="",
            context="",
            boy_or_girl="",
            batch=batch,
            has_person="no",
            source_row_hash=f"hash_{job_id}_{index}",
        )
        item = CsvJobItem(
            id=f"csvitm_{job_id}_{index}",
            csv_job_id=job.id,
            entry_id=entry.id,
            row_index=index,
            status=row_status,
        )
        db_session.add_all([entry, item])
        db_session.add(
            CsvTaskNode(
                id=f"csvtsk_{job_id}_{index}",
                csv_job_id=job.id,
                csv_job_item_id=item.id,
                step_name="step1_base",
                task_key=f"{job_id}-{index}",
                status=task_status,
                finished_at=finished,
            )
        )
    db_session.commit()
    return job


def test_writes_are_refused_when_allowlist_is_empty(db_session) -> None:
    _configure(allowed="")
    service = SlackService(db_session)

    assert service.authorized("U_ANY", write=False) is True
    assert service.authorized("U_ANY", write=True) is False

    reply = service.dm_response_text("stop", user_id="U_ANY", base_url="https://example.com")
    assert "SLACK_ALLOWED_USER_IDS" in reply


def test_reads_still_work_when_allowlist_is_empty(db_session) -> None:
    _configure(allowed="")
    service = SlackService(db_session)
    reply = service.dm_response_text("health", user_id="U_ANY", base_url="https://example.com")
    assert "Verbali health" in reply


def test_stop_without_job_id_resolves_the_running_job(db_session) -> None:
    _configure(allowed="U_ALLOWED")
    _make_job(
        db_session, job_id="csvjob_running", status="running", batch="batch_running", pending_rows=1
    )
    service = SlackService(db_session)

    reply = service.dm_response_text("stop", user_id="U_ALLOWED", base_url="https://example.com")

    assert "csvjob_running" in reply
    assert "still bill" in reply
    db_session.expire_all()
    # The queued task is cancelled and the job leaves the running state. The
    # exact terminal label is the finalizer's business, not the bot's.
    assert db_session.get(CsvTaskNode, "csvtsk_csvjob_running_2").status == "canceled"
    assert db_session.get(CsvJob, "csvjob_running").status not in {"running", "queued", "retry_queued"}


def test_stop_asks_which_job_when_several_are_running(db_session) -> None:
    _configure(allowed="U_ALLOWED")
    _make_job(db_session, job_id="csvjob_one", status="running", batch="batch_one")
    _make_job(db_session, job_id="csvjob_two", status="queued", batch="batch_two")
    service = SlackService(db_session)

    reply = service.dm_response_text("stop", user_id="U_ALLOWED", base_url="https://example.com")

    assert "More than one job is running" in reply
    assert "csvjob_one" in reply and "csvjob_two" in reply
    db_session.expire_all()
    assert db_session.get(CsvJob, "csvjob_one").status == "running"


def test_stop_reports_when_nothing_is_running(db_session) -> None:
    _configure(allowed="U_ALLOWED")
    _make_job(db_session, job_id="csvjob_done", status="completed", batch="batch_done")
    service = SlackService(db_session)

    reply = service.dm_response_text("stop", user_id="U_ALLOWED", base_url="https://example.com")

    assert "No job is running" in reply


def test_status_without_id_reports_progress_and_last_image(db_session) -> None:
    _configure(allowed="U_ALLOWED")
    _make_job(db_session, job_id="csvjob_running", status="running", batch="batch_running")
    service = SlackService(db_session)

    reply = service.dm_response_text("status", user_id="U_ALLOWED", base_url="https://example.com")

    assert "csvjob_running" in reply
    assert "Progress:" in reply
    assert "1 done" in reply
    assert "Last image:" in reply
    assert "m ago" in reply


def test_status_falls_back_to_most_recent_job_when_idle(db_session) -> None:
    _configure(allowed="U_ALLOWED")
    _make_job(db_session, job_id="csvjob_done", status="partial_failed", batch="batch_done")
    service = SlackService(db_session)

    reply = service.dm_response_text("status", user_id="U_ALLOWED", base_url="https://example.com")

    assert "Nothing is running" in reply
    assert "csvjob_done" in reply


def test_start_without_id_lists_candidates_instead_of_guessing(db_session) -> None:
    _configure(allowed="U_ALLOWED")
    _make_job(db_session, job_id="csvjob_pending", status="imported", batch="batch_pending")
    service = SlackService(db_session)

    reply = service.dm_response_text("start", user_id="U_ALLOWED", base_url="https://example.com")

    assert "Name the job to start" in reply
    assert "csvjob_pending" in reply
    db_session.expire_all()
    assert db_session.get(CsvJob, "csvjob_pending").status == "imported"


def test_retry_requeues_failed_tasks_on_the_most_recent_job(db_session) -> None:
    _configure(allowed="U_ALLOWED")
    _make_job(db_session, job_id="csvjob_failed", status="partial_failed", batch="batch_failed")
    service = SlackService(db_session)

    reply = service.dm_response_text("retry", user_id="U_ALLOWED", base_url="https://example.com")

    assert "csvjob_failed" in reply
    assert "Requeued 1" in reply


def test_health_separates_running_from_imported(db_session) -> None:
    _configure(allowed="U_ALLOWED")
    _make_job(db_session, job_id="csvjob_pending", status="imported", batch="batch_pending")
    service = SlackService(db_session)

    reply = service.dm_response_text("health", user_id="U_ALLOWED", base_url="https://example.com")

    assert "Running CSV jobs: 0" in reply
    assert "Imported, not started: 1" in reply
