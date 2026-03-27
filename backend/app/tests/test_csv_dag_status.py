from app.services.csv_dag_service import CsvDagService
from app.services.repository import Repository


def _make_entry(repo: Repository, *, word: str = "soccer"):
    return repo.create_entry(
        {
            "word": word,
            "part_of_sentence": "noun",
            "category": "sport",
            "context": "",
            "boy_or_girl": "",
            "batch": "test",
        }
    )


def test_item_progress_treats_started_queued_task_as_running(db_session) -> None:
    repo = Repository(db_session)
    service = CsvDagService(db_session)
    entry = _make_entry(repo)
    job = repo.create_csv_job(
        batch_id="csv_test_status",
        source_file_name="test.csv",
        execution_mode="csv_dag",
        config_snapshot={},
    )
    item = repo.create_csv_job_item(
        csv_job_id=job.id,
        entry_id=entry.id,
        row_index=1,
        source_row={"word": "soccer"},
    )
    task = repo.create_csv_task_node(
        csv_job_id=job.id,
        csv_job_item_id=item.id,
        step_name="step1_base",
        task_key="row1:base",
        profile_key="male:kid:white",
        source_profile_key="",
        branch_role="base",
        dependency_keys=[],
        dependency_task_ids=[],
        status="queued",
    )

    payload = service._item_progress_payload(item, [task])

    assert payload["main_status"] == "running"
    assert payload["current_step"] == "Base images"
    assert "Queued for Base images" in payload["sub_status"]


def test_serialize_job_exposes_ui_facing_display_status(db_session) -> None:
    repo = Repository(db_session)
    service = CsvDagService(db_session)
    job = repo.create_csv_job(
        batch_id="csv_test_job_display",
        source_file_name="test.csv",
        execution_mode="csv_dag",
        config_snapshot={},
    )

    imported = service._serialize_job(job, {"total_row_count": 3, "duration_seconds": 0})
    assert imported["display_status"] == "pending"
    assert imported["display_sub_status"] == "Imported and not started yet"

    queued_job = repo.update_csv_job(job, status="queued")
    queued = service._serialize_job(queued_job, {"total_row_count": 3, "duration_seconds": 0})
    assert queued["display_status"] == "running"
    assert queued["display_sub_status"] == "Queued under load"

    failed_job = repo.update_csv_job(job, status="failed")
    failed = service._serialize_job(failed_job, {"total_row_count": 3, "duration_seconds": 0})
    assert failed["display_status"] == "failure"
    assert failed["display_sub_status"] == "One or more rows failed"


def test_start_job_records_started_at_even_before_first_claim(db_session) -> None:
    repo = Repository(db_session)
    service = CsvDagService(db_session)
    entry = _make_entry(repo, word="bucket")
    job = repo.create_csv_job(
        batch_id="csv_test_start_time",
        source_file_name="test.csv",
        execution_mode="csv_dag",
        config_snapshot={},
    )
    item = repo.create_csv_job_item(
        csv_job_id=job.id,
        entry_id=entry.id,
        row_index=1,
        source_row={"word": "bucket"},
    )
    repo.create_csv_task_node(
        csv_job_id=job.id,
        csv_job_item_id=item.id,
        step_name="step1_base",
        task_key="row1:base",
        profile_key="male:kid:white",
        source_profile_key="",
        branch_role="base",
        dependency_keys=[],
        dependency_task_ids=[],
        status="pending",
    )

    started = service.start_job(job.id)

    assert started.started_at is not None
    assert started.status == "queued"
