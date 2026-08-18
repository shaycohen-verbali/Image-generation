from datetime import datetime, timedelta

from app.services.csv_dag_service import CsvDagService
from app.services.job_summary_service import JobSummaryService, PRICING_VERSION
from app.services.repository import Repository


def test_terminal_job_without_aggregate_returns_unavailable_placeholder(db_session, monkeypatch) -> None:
    repo = Repository(db_session)
    job = repo.create_csv_job(
        batch_id="missing_summary",
        source_file_name="test.csv",
        execution_mode="csv_dag",
        config_snapshot={},
    )
    now = datetime.utcnow()
    repo.update_csv_job(job, status="partial_failed", started_at=now, finished_at=now)
    monkeypatch.setattr(JobSummaryService, "finalize_if_terminal", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("finalizer on read")))
    monkeypatch.setattr(JobSummaryService, "live_details", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("live calculation on read")))

    payload = CsvDagService(db_session).job_summary(job.id)
    details = CsvDagService(db_session).job_summary_details(job.id)

    assert payload is not None
    assert payload["job_summary"]["available"] is False
    assert payload["job_summary"]["generation_status"] == "missing"
    assert payload["job_summary"]["total_cost_usd"] is None
    assert details is not None
    assert details["available"] is False


def test_final_job_summary_is_fixed_estimated_and_read_without_history_scan(db_session, monkeypatch) -> None:
    repo = Repository(db_session)
    entry = repo.create_entry({"word": "summary", "part_of_sentence": "noun", "category": "", "batch": "test"})
    run = repo.create_runs([entry.id], quality_threshold=95, max_optimization_attempts=3, execution_mode="csv_shadow")[0]
    repo.add_stage_result(
        run_id=run.id, stage_name="stage2_draft", attempt=1, status="completed", idempotency_key="summary:draft",
        request_json={}, response_json={"model": "black-forest-labs/flux-schnell"},
    )
    job = repo.create_csv_job(batch_id="summary_job", source_file_name="test.csv", execution_mode="csv_dag", config_snapshot={})
    item = repo.create_csv_job_item(csv_job_id=job.id, entry_id=entry.id, row_index=1, source_row={})
    item = repo.update_csv_job_item(item, shadow_run_id=run.id, status="completed")
    task = repo.create_csv_task_node(
        csv_job_id=job.id, csv_job_item_id=item.id, step_name="step1_base", task_key="summary:base",
        profile_key="male:kid:white", source_profile_key="", branch_role="base",
        dependency_keys=[], dependency_task_ids=[], status="completed",
    )
    started = datetime.utcnow() - timedelta(seconds=15)
    repo.update_csv_task(task, started_at=started, finished_at=started + timedelta(seconds=10), attempt_count=2)
    repo.add_csv_task_attempt(
        csv_task_node_id=task.id, attempt_number=2, status="completed", request_json={}, response_json={},
        finished_at=started + timedelta(seconds=10),
    )
    job = repo.update_csv_job(job, status="completed", started_at=started, finished_at=started + timedelta(seconds=15))

    summary = JobSummaryService(db_session).finalize_if_terminal(job.id)

    assert summary is not None
    assert summary["is_final"] is True
    assert summary["counts"] == {"completed": 1, "failed": 0, "skipped": 0, "queued": 0, "running": 0}
    assert summary["wall_clock_seconds"] == 15
    assert summary["cost_basis"] == "estimated"
    assert summary["pricing_version"] == PRICING_VERSION
    assert summary["total_cost_usd"] == 0.003

    monkeypatch.setattr(Repository, "get_run_cost_inputs_by_ids", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("history scan")))
    polled = CsvDagService(db_session).job_summary(job.id)
    assert polled is not None
    assert polled["job_summary"] == summary

    unchanged = JobSummaryService(db_session).finalize_if_terminal(job.id)
    assert unchanged == summary


def test_no_person_variant_is_an_informational_skipped_subset(db_session) -> None:
    repo = Repository(db_session)
    entry = repo.create_entry({"word": "ace", "part_of_sentence": "noun", "category": "", "batch": "test"})
    job = repo.create_csv_job(batch_id="no_person_summary", source_file_name="test.csv", execution_mode="csv_dag", config_snapshot={})
    item = repo.create_csv_job_item(csv_job_id=job.id, entry_id=entry.id, row_index=1, source_row={})
    repo.update_csv_job_item(item, status="completed")
    repo.create_csv_task_node(
        csv_job_id=job.id, csv_job_item_id=item.id, step_name="step1_base", task_key="ace:base",
        profile_key="male:kid:white", source_profile_key="", branch_role="base",
        dependency_keys=[], dependency_task_ids=[], status="completed",
    )
    variant = repo.create_csv_task_node(
        csv_job_id=job.id, csv_job_item_id=item.id, step_name="step2_variant", task_key="ace:variant",
        profile_key="female:kid:white", source_profile_key="male:kid:white", branch_role="variant",
        dependency_keys=[], dependency_task_ids=[], status="completed",
    )
    repo.update_csv_task(variant, error_summary="No person required for this word")
    now = datetime.utcnow()
    repo.update_csv_job(job, status="completed", started_at=now, finished_at=now)

    summary = JobSummaryService(db_session).finalize_if_terminal(job.id)

    assert summary is not None
    assert summary["counts"]["completed"] == 1
    assert summary["counts"]["skipped"] == 1


def test_terminal_summary_batches_run_ids_and_reads_compact_rows_only(db_session, monkeypatch) -> None:
    repo = Repository(db_session)
    entries = [
        repo.create_entry({"word": f"batch-{index}", "part_of_sentence": "noun", "category": "", "batch": "test"})
        for index in range(51)
    ]
    runs = repo.create_runs(
        [entry.id for entry in entries],
        quality_threshold=95,
        max_optimization_attempts=3,
        execution_mode="csv_shadow",
    )
    job = repo.create_csv_job(batch_id="summary_batches", source_file_name="test.csv", execution_mode="csv_dag", config_snapshot={})
    for index, (entry, run) in enumerate(zip(entries, runs), start=1):
        item = repo.create_csv_job_item(csv_job_id=job.id, entry_id=entry.id, row_index=index, source_row={})
        repo.update_csv_job_item(item, shadow_run_id=run.id, status="completed")
    now = datetime.utcnow()
    repo.update_csv_job(job, status="completed", started_at=now, finished_at=now)

    calls = []
    original = Repository.get_run_cost_inputs_by_ids

    def record_batches(self, run_ids, *, include_stage_payloads=True):
        calls.append((len(run_ids), include_stage_payloads))
        return original(self, run_ids, include_stage_payloads=include_stage_payloads)

    monkeypatch.setattr(Repository, "get_run_cost_inputs_by_ids", record_batches)
    summary = JobSummaryService(db_session).finalize_if_terminal(job.id)

    assert summary is not None
    assert [size for size, _include_payloads in calls] == [25, 25, 1]
    assert all(include_payloads is False for _size, include_payloads in calls)
    assert summary["counts"]["completed"] == 51
    assert summary["cost_basis"] == "unavailable"
    assert summary["total_cost_usd"] is None


def test_running_job_summary_does_not_calculate_cost_on_read(db_session, monkeypatch) -> None:
    repo = Repository(db_session)
    entry = repo.create_entry({"word": "running", "part_of_sentence": "noun", "category": "", "batch": "test"})
    job = repo.create_csv_job(batch_id="running_summary", source_file_name="test.csv", execution_mode="csv_dag", config_snapshot={})
    repo.update_csv_job(job, status="running", started_at=datetime.utcnow())
    run = repo.create_runs([entry.id], quality_threshold=95, max_optimization_attempts=3, execution_mode="csv_shadow")[0]
    repo.add_stage_result(
        run_id=run.id, stage_name="stage2_draft", attempt=1, status="completed", idempotency_key="running:draft",
        request_json={}, response_json={"model": "black-forest-labs/flux-schnell"},
    )
    item = repo.create_csv_job_item(csv_job_id=job.id, entry_id=entry.id, row_index=1, source_row={})
    repo.update_csv_job_item(item, shadow_run_id=run.id, status="running")

    service = CsvDagService(db_session)
    monkeypatch.setattr(JobSummaryService, "finalize_if_terminal", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("finalizer on read")))
    monkeypatch.setattr(JobSummaryService, "live_details", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("live calculation on read")))
    payload = service.job_summary(job.id)
    details = service.job_summary_details(job.id)

    assert payload is not None
    assert payload["job_summary"]["is_final"] is False
    assert payload["job_summary"]["available"] is False
    assert payload["job_summary"]["generation_status"] == "not_requested"
    assert payload["job_summary"]["counts"]["skipped"] is None
    assert payload["job_summary"]["total_cost_usd"] is None
    assert payload["job_summary"]["cost_basis"] == "unavailable"
    assert details is not None
    assert details["available"] is False
    assert details["cost"]["cost_by_provider"] == {}
