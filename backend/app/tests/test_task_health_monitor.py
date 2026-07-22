from datetime import datetime, timedelta

from app.services.repository import Repository
from app.services.task_health_monitor import TaskHealthMonitor


def test_task_health_monitor_uses_one_query_and_skips_overlapping_cycles(db_session, monkeypatch) -> None:
    repo = Repository(db_session)
    entry = repo.create_entry({"word": "monitor", "part_of_sentence": "noun", "category": "", "batch": "test"})
    job = repo.create_csv_job(batch_id="monitor_job", source_file_name="test.csv", execution_mode="csv_dag", config_snapshot={})
    item = repo.create_csv_job_item(csv_job_id=job.id, entry_id=entry.id, row_index=1, source_row={})
    task = repo.create_csv_task_node(
        csv_job_id=job.id, csv_job_item_id=item.id, step_name="step1_base", task_key="monitor:base",
        profile_key="male:kid:white", source_profile_key="", branch_role="base",
        dependency_keys=[], dependency_task_ids=[], status="running",
    )
    repo.update_csv_task(task, started_at=datetime.utcnow() - timedelta(minutes=8))
    calls = 0
    original_execute = db_session.execute

    def counted_execute(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_execute(*args, **kwargs)

    monkeypatch.setattr(db_session, "execute", counted_execute)
    monitor = TaskHealthMonitor(interval_seconds=300, timeout_ms=1000, stale_seconds=420)

    summary = monitor.maybe_emit(db_session, now_monotonic=1000)
    skipped = monitor.maybe_emit(db_session, now_monotonic=1001)

    assert calls == 1
    assert summary is not None
    assert summary["running_tasks"] == 1
    assert summary["stale_tasks"] == 1
    assert summary["oldest_running_age_seconds"] >= 479
    assert summary["worker_heartbeat_age_seconds"] is None
    assert skipped is None
