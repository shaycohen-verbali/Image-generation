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


def test_item_progress_uses_item_status_when_no_tasks_exist(db_session) -> None:
    repo = Repository(db_session)
    service = CsvDagService(db_session)
    entry = _make_entry(repo, word="aggressive")
    job = repo.create_csv_job(
        batch_id="csv_test_item_no_tasks",
        source_file_name="test.csv",
        execution_mode="csv_dag",
        config_snapshot={},
    )
    item = repo.create_csv_job_item(
        csv_job_id=job.id,
        entry_id=entry.id,
        row_index=1,
        source_row={"word": "aggressive"},
        status="completed",
        error_detail="Requested variants already exist in inventory",
    )

    payload = service._item_progress_payload(item, [])

    assert payload["main_status"] == "completed"
    assert payload["sub_status"] == "Requested variants already exist in inventory"
    assert payload["progress"] == {
        "completed": 0,
        "total": 0,
        "running": 0,
        "waiting": 0,
        "failed": 0,
        "canceled": 0,
    }


def test_finalize_job_does_not_mark_pending_taskless_items_completed(db_session) -> None:
    repo = Repository(db_session)
    entry = _make_entry(repo, word="abbey")
    job = repo.create_csv_job(
        batch_id="csv_test_taskless_pending",
        source_file_name="test.csv",
        execution_mode="csv_dag",
        config_snapshot={},
    )
    repo.create_csv_job_item(
        csv_job_id=job.id,
        entry_id=entry.id,
        row_index=1,
        source_row={"word": "abbey"},
        status="pending",
    )

    finalized = repo.finalize_csv_job_status(job.id)

    assert finalized is not None
    assert finalized.status == "imported"


def test_variant_task_prefers_softened_base_asset_when_present(db_session, monkeypatch) -> None:
    repo = Repository(db_session)
    service = CsvDagService(db_session)
    entry = _make_entry(repo, word="abbey")
    shadow_run = repo.create_shadow_run(
        entry_id=entry.id,
        quality_threshold=95,
        max_optimization_attempts=3,
    )
    job = repo.create_csv_job(
        batch_id="csv_test_soften_dependency",
        source_file_name="test.csv",
        execution_mode="csv_dag",
        config_snapshot={
            "image_aspect_ratio": "1:1",
            "image_resolution": "1K",
            "image_format": "jpg",
            "nano_banana_safety_level": "default",
        },
    )
    item = repo.create_csv_job_item(
        csv_job_id=job.id,
        entry_id=entry.id,
        row_index=1,
        source_row={"word": "abbey"},
        shadow_run_id=shadow_run.id,
    )
    quality_asset = repo.add_asset(
        run_id=shadow_run.id,
        stage_name="stage3_upgraded",
        attempt=1,
        file_name="quality.jpg",
        abs_path="/tmp/quality.jpg",
        mime_type="image/jpeg",
        sha256="quality",
        width=100,
        height=100,
        origin_url="",
        model_name="test",
    )
    soften_asset = repo.add_asset(
        run_id=shadow_run.id,
        stage_name="stage3_post_quality_accessibility_generate",
        attempt=1,
        file_name="soften.jpg",
        abs_path="/tmp/soften.jpg",
        mime_type="image/jpeg",
        sha256="soften",
        width=100,
        height=100,
        origin_url="",
        model_name="test",
    )
    variant_regular_asset = repo.add_asset(
        run_id=shadow_run.id,
        stage_name="stage4_variant_generate",
        attempt=1,
        file_name="variant-regular.jpg",
        abs_path="/tmp/variant-regular.jpg",
        mime_type="image/jpeg",
        sha256="variant-regular",
        width=100,
        height=100,
        origin_url="",
        model_name="test",
    )
    variant_white_bg_asset = repo.add_asset(
        run_id=shadow_run.id,
        stage_name="stage5_variant_white_bg",
        attempt=1,
        file_name="variant-white.jpg",
        abs_path="/tmp/variant-white.jpg",
        mime_type="image/jpeg",
        sha256="variant-white",
        width=100,
        height=100,
        origin_url="",
        model_name="test",
    )
    repo.update_csv_job_item(
        item,
        base_regular_asset_id=quality_asset.id,
        base_soften_asset_id=soften_asset.id,
        base_white_bg_asset_id=variant_white_bg_asset.id,
    )
    base_task = repo.create_csv_task_node(
        csv_job_id=job.id,
        csv_job_item_id=item.id,
        step_name="step1_base",
        task_key="row1:base",
        profile_key="male:kid:white",
        source_profile_key="",
        branch_role="base",
        dependency_keys=[],
        dependency_task_ids=[],
        status="completed",
        regular_asset_id=quality_asset.id,
        white_bg_asset_id=variant_white_bg_asset.id,
    )
    variant_task = repo.create_csv_task_node(
        csv_job_id=job.id,
        csv_job_item_id=item.id,
        step_name="step2_variant",
        task_key="row1:variant",
        profile_key="male:tween:white",
        source_profile_key="male:kid:white",
        branch_role="male_age_variant",
        dependency_keys=[base_task.task_key],
        dependency_task_ids=[base_task.id],
        status="running",
    )

    captured: dict[str, str] = {}

    class FakePipelineRunner:
        def __init__(self, db):
            self.db = db

        def create_profile_variant_pair(self, **kwargs):
            source_asset = kwargs["source_asset"]
            captured["source_asset_id"] = getattr(source_asset, "id", "")
            return {
                "regular_asset": variant_regular_asset,
                "white_bg_asset": variant_white_bg_asset,
            }

    monkeypatch.setattr("app.services.csv_dag_service.PipelineRunner", FakePipelineRunner)

    finished = service.execute_task(variant_task.id)

    assert captured["source_asset_id"] == soften_asset.id
    assert finished.status == "completed"


def test_job_overview_recovers_completed_base_shadow_run(db_session) -> None:
    repo = Repository(db_session)
    service = CsvDagService(db_session)
    entry = _make_entry(repo, word="ladder")
    shadow_run = repo.create_shadow_run(
        entry_id=entry.id,
        quality_threshold=95,
        max_optimization_attempts=3,
    )
    repo.update_run(
        shadow_run,
        status="completed_base_assets",
        current_stage="completed_base_assets",
        optimization_attempt=1,
        quality_score=97,
    )
    quality_asset = repo.add_asset(
        run_id=shadow_run.id,
        stage_name="stage3_upgraded",
        attempt=1,
        file_name="quality.jpg",
        abs_path="/tmp/recovered-quality.jpg",
        mime_type="image/jpeg",
        sha256="recovered-quality",
        width=100,
        height=100,
        origin_url="",
        model_name="test",
    )
    soften_asset = repo.add_asset(
        run_id=shadow_run.id,
        stage_name="stage3_post_quality_accessibility_generate",
        attempt=1,
        file_name="soften.jpg",
        abs_path="/tmp/recovered-soften.jpg",
        mime_type="image/jpeg",
        sha256="recovered-soften",
        width=100,
        height=100,
        origin_url="",
        model_name="test",
    )
    white_bg_asset = repo.add_asset(
        run_id=shadow_run.id,
        stage_name="stage4_white_bg",
        attempt=1,
        file_name="white.jpg",
        abs_path="/tmp/recovered-white.jpg",
        mime_type="image/jpeg",
        sha256="recovered-white",
        width=100,
        height=100,
        origin_url="",
        model_name="test",
    )
    job = repo.create_csv_job(
        batch_id="csv_test_recover_success",
        source_file_name="test.csv",
        execution_mode="csv_dag",
        config_snapshot={},
    )
    item = repo.create_csv_job_item(
        csv_job_id=job.id,
        entry_id=entry.id,
        row_index=1,
        source_row={"word": "ladder"},
        shadow_run_id=shadow_run.id,
        status="running",
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
        status="running",
    )

    overview = service.job_overview(job.id)

    refreshed_task = repo.list_csv_tasks(job.id)[0]
    refreshed_item = repo.get_csv_job_item(item.id)
    refreshed_job = repo.get_csv_job(job.id)

    assert overview is not None
    assert refreshed_task.status == "completed"
    assert refreshed_task.regular_asset_id == quality_asset.id
    assert refreshed_task.white_bg_asset_id == white_bg_asset.id
    assert refreshed_item is not None
    assert refreshed_item.status == "completed"
    assert refreshed_item.base_regular_asset_id == quality_asset.id
    assert refreshed_item.base_soften_asset_id == soften_asset.id
    assert refreshed_item.base_white_bg_asset_id == white_bg_asset.id
    assert refreshed_job is not None
    assert refreshed_job.status == "completed"


def test_loop_count_uses_highest_scored_attempt_not_winner_attempt() -> None:
    class ScoreRow:
        def __init__(self, attempt):
            self.attempt = attempt

    class StageRow:
        def __init__(self, stage_name, attempt):
            self.stage_name = stage_name
            self.attempt = attempt

    snapshot = {
        "scores": [ScoreRow(1), ScoreRow(2), ScoreRow(3)],
        "stages": [StageRow("stage3_upgrade", 1), StageRow("quality_gate", 3)],
    }

    loop_count = CsvDagService._loop_count_from_snapshot(snapshot, 1)

    assert loop_count == 3
