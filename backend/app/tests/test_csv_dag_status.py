import csv
import json
import zipfile
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

from app.services.csv_dag_service import (
    CsvDagService,
    _dependency_profile_for,
    _extract_google_image_safety_details,
    _friendly_variant_error_summary,
)
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


def test_job_summary_uses_only_aggregate_status_data(db_session) -> None:
    repo = Repository(db_session)
    service = CsvDagService(db_session)
    entry = _make_entry(repo, word="summary")
    job = repo.create_csv_job(
        batch_id="csv_test_summary",
        source_file_name="test.csv",
        execution_mode="csv_dag",
        config_snapshot={},
    )
    item = repo.create_csv_job_item(
        csv_job_id=job.id,
        entry_id=entry.id,
        row_index=1,
        source_row={"word": "summary"},
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
    repo.update_csv_job_item(item, status="running")

    summary = service.job_summary(job.id)

    assert summary is not None
    assert summary["job"]["total_row_count"] == 1
    assert summary["word_counts"]["running"] == 1
    assert summary["step_counts"] == {"step1_base": {"running": 1}}
    assert summary["last_progress_at"] is not None
    assert summary["export_ready"] is False


def test_job_items_page_bounds_items_and_tasks(db_session) -> None:
    repo = Repository(db_session)
    service = CsvDagService(db_session)
    job = repo.create_csv_job(
        batch_id="csv_test_page", source_file_name="test.csv", execution_mode="csv_dag", config_snapshot={}
    )
    for row_index in range(1, 4):
        entry = _make_entry(repo, word=f"word-{row_index}")
        item = repo.create_csv_job_item(
            csv_job_id=job.id, entry_id=entry.id, row_index=row_index, source_row={"word": entry.word}
        )
        repo.create_csv_task_node(
            csv_job_id=job.id, csv_job_item_id=item.id, step_name="step1_base",
            task_key=f"row{row_index}:base", profile_key="male:kid:white", source_profile_key="",
            branch_role="base", dependency_keys=[], dependency_task_ids=[], status="pending",
        )

    page = service.job_items_page(job.id, offset=1, limit=1)

    assert page is not None
    assert page["total"] == 3
    assert page["offset"] == 1
    assert [item["row_index"] for item in page["items"]] == [2]
    assert page["tasks"] == []

    detail = service.job_item_detail(job.id, page["items"][0]["id"])
    assert detail is not None
    assert len(detail["tasks"]) == 1
    assert detail["tasks"][0]["csv_job_item_id"] == detail["item"]["id"]

    metadata = service.job_metadata(job.id)
    assert metadata is not None
    assert metadata["job"]["total_row_count"] == 3
    assert metadata["items"] == []
    assert metadata["tasks"] == []


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


def test_stale_csv_task_timeout_excludes_work_owned_by_live_worker(db_session) -> None:
    repo = Repository(db_session)
    entry = _make_entry(repo, word="abby")
    job = repo.create_csv_job(
        batch_id="csv_test_live_task_timeout",
        source_file_name="test.csv",
        execution_mode="csv_dag",
        config_snapshot={},
    )
    item = repo.create_csv_job_item(
        csv_job_id=job.id,
        entry_id=entry.id,
        row_index=22,
        source_row={"word": "abby"},
    )
    task = repo.create_csv_task_node(
        csv_job_id=job.id,
        csv_job_item_id=item.id,
        step_name="step1_base",
        task_key="row22:base",
        profile_key="male:kid:white",
        source_profile_key="",
        branch_role="base",
        dependency_keys=[],
        dependency_task_ids=[],
        status="running",
    )
    repo.update_csv_task(task, started_at=datetime.utcnow() - timedelta(minutes=20))

    timed_out_ids = repo.fail_stale_running_csv_tasks(
        timeout_seconds=420,
        exclude_task_ids={task.id},
    )

    assert timed_out_ids == []
    assert repo.get_csv_task(task.id).status == "running"


def test_stale_csv_task_timeout_still_recovers_orphaned_work(db_session) -> None:
    repo = Repository(db_session)
    entry = _make_entry(repo, word="orphan")
    job = repo.create_csv_job(
        batch_id="csv_test_orphan_task_timeout",
        source_file_name="test.csv",
        execution_mode="csv_dag",
        config_snapshot={},
    )
    item = repo.create_csv_job_item(
        csv_job_id=job.id,
        entry_id=entry.id,
        row_index=1,
        source_row={"word": "orphan"},
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
        status="running",
    )
    repo.update_csv_task(task, started_at=datetime.utcnow() - timedelta(minutes=20))

    timed_out_ids = repo.fail_stale_running_csv_tasks(timeout_seconds=420)

    assert timed_out_ids == [task.id]
    refreshed = repo.get_csv_task(task.id)
    assert refreshed.status == "failed"
    assert refreshed.error_summary == "Timed out after 420 seconds"


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
    )
    item = repo.update_csv_job_item(
        item,
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


def test_continued_job_marks_parent_completed_word_as_previously_done(db_session) -> None:
    repo = Repository(db_session)
    service = CsvDagService(db_session)
    entry = _make_entry(repo, word="again")
    parent_job = repo.create_csv_job(
        batch_id="csv_parent_round",
        source_file_name="test.csv",
        execution_mode="csv_dag",
        config_snapshot={},
    )
    parent_item = repo.create_csv_job_item(
        csv_job_id=parent_job.id,
        entry_id=entry.id,
        row_index=1,
        source_row={"word": "again"},
    )
    repo.update_csv_job_item(parent_item, status="completed")

    continued_job = repo.create_csv_job(
        batch_id="csv_continued_round",
        source_file_name="test.csv",
        execution_mode="csv_dag",
        config_snapshot={"continued_from_job_id": parent_job.id},
    )
    continued_item = repo.create_csv_job_item(
        csv_job_id=continued_job.id,
        entry_id=entry.id,
        row_index=1,
        source_row={"word": "again"},
    )
    repo.create_csv_task_node(
        csv_job_id=continued_job.id,
        csv_job_item_id=continued_item.id,
        step_name="step2_variant",
        task_key="row1:variant",
        profile_key="female:kid:white",
        source_profile_key="male:kid:white",
        branch_role="variant",
        dependency_keys=[],
        dependency_task_ids=[],
        status="pending",
    )

    overview = service.job_overview(continued_job.id)

    assert overview is not None
    assert overview["items"][0]["main_status"] == "previously_done"
    assert overview["word_counts"]["previously_done"] == 1
    assert overview["word_counts"]["pending"] == 0


def test_finalize_job_does_not_mark_pending_taskless_items_completed(db_session) -> None:
    repo = Repository(db_session)
    entry = _make_entry(repo, word="abbey")
    job = repo.create_csv_job(
        batch_id="csv_test_taskless_pending",
        source_file_name="test.csv",
        execution_mode="csv_dag",
        config_snapshot={},
    )
    item = repo.create_csv_job_item(
        csv_job_id=job.id,
        entry_id=entry.id,
        row_index=1,
        source_row={"word": "abbey"},
    )
    repo.update_csv_job_item(item, status="pending")

    finalized = repo.finalize_csv_job_status(job.id)

    assert finalized is not None
    assert finalized.status == "imported"


def test_white_female_teenager_depends_on_white_male_teenager() -> None:
    dependency = _dependency_profile_for({"gender": "female", "age": "teenager", "skin_color": "white"})
    assert dependency == {"gender": "male", "age": "teenager", "skin_color": "white"}


def test_export_job_packages_inventory_selected_images(db_session, tmp_path, monkeypatch) -> None:
    repo = Repository(db_session)
    service = CsvDagService(db_session)
    entry = _make_entry(repo, word="fairly")
    job = repo.create_csv_job(
        batch_id="csv_test_export_inventory",
        source_file_name="test.csv",
        execution_mode="csv_dag",
        config_snapshot={
            "person_gender_options": ["female"],
            "person_age_options": ["teenager"],
            "person_skin_color_options": ["white"],
        },
    )
    item = repo.create_csv_job_item(
        csv_job_id=job.id,
        entry_id=entry.id,
        row_index=1,
        source_row={"word": "fairly"},
    )
    repo.update_csv_job_item(item, status="completed")

    image_path = tmp_path / "teen-regular.jpg"
    image_path.write_bytes(b"teen-regular")

    monkeypatch.setattr("app.services.csv_dag_service.InventorySyncService.sync_csv_job", lambda self, job_id: 0)
    monkeypatch.setattr(
        "app.services.csv_dag_service.InventorySyncService.build_export_rows",
        lambda self, job_id: [
            {
                "row_index": 1,
                "word": "fairly",
                "part_of_sentence": "noun",
                "category": "sport",
                "sense_id": "sense-fairly-noun-1",
                "context": "",
                "job_status": "completed",
                "fully_complete": True,
                "missing_slots_json": "[]",
                "failure_reasons_json": "[]",
                "teenager_female_white_regular_path": image_path.as_posix(),
                "teenager_female_white_regular_prompt": "Make a clear teenager image.",
            }
        ],
    )

    result = service.export_job(
        job.id,
        export_fields=[
            "row_index",
            "word",
            "part_of_sentence",
            "category",
            "context",
            "job_status",
            "fully_complete",
            "missing_slots_json",
            "failure_reasons_json",
            "teenager_female_white_regular_path",
        ],
    )

    zip_path = Path(result["local_zip_path"])
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        images_rows = list(csv.DictReader(StringIO(archive.read("images.csv").decode("utf-8"))))
        prompts_rows = list(csv.DictReader(StringIO(archive.read("prompts.csv").decode("utf-8"))))
        assert "README.md" in names
        assert "images.csv" in names
        assert "prompts.csv" in names
        assert "_metadata/job_summary.csv" in names
        assert "_metadata/word_inventory_legacy.csv" in names
        assert "_metadata/manifest.json" in names
        assert "images/regular/0001__fairly__noun__sense-fairly-noun-1__f_tn_w_reg.jpg" in names
    assert images_rows[0]["image_relative_path"] == "images/regular/0001__fairly__noun__sense-fairly-noun-1__f_tn_w_reg.jpg"
    assert images_rows[0]["variant_abbrev"] == "f_tn_w_reg"
    assert prompts_rows[0]["word"] == "fairly"
    assert prompts_rows[0]["part_of_sentence"] == "noun"
    assert prompts_rows[0]["category"] == "sport"
    assert prompts_rows[0]["image_filename"] == "0001__fairly__noun__sense-fairly-noun-1__f_tn_w_reg.jpg"
    assert prompts_rows[0]["prompt_text"] == "Make a clear teenager image."


def test_export_job_skips_missing_images_and_records_warning(db_session, tmp_path, monkeypatch) -> None:
    repo = Repository(db_session)
    service = CsvDagService(db_session)
    entry = _make_entry(repo, word="gentle")
    job = repo.create_csv_job(
        batch_id="csv_test_export_missing",
        source_file_name="test.csv",
        execution_mode="csv_dag",
        config_snapshot={
            "person_gender_options": ["female"],
            "person_age_options": ["teenager"],
            "person_skin_color_options": ["white"],
        },
    )
    item = repo.create_csv_job_item(
        csv_job_id=job.id,
        entry_id=entry.id,
        row_index=1,
        source_row={"word": "gentle"},
    )
    repo.update_csv_job_item(item, status="completed")

    valid_image = tmp_path / "valid.jpg"
    valid_image.write_bytes(b"valid-image")

    monkeypatch.setattr(
        "app.services.csv_dag_service.InventorySyncService.sync_csv_job",
        lambda self, job_id: (_ for _ in ()).throw(RuntimeError("inventory unavailable")),
    )
    monkeypatch.setattr(
        "app.services.csv_dag_service.InventorySyncService.build_export_rows",
        lambda self, job_id: [
            {
                "row_index": 1,
                "word": "gentle",
                "part_of_sentence": "noun",
                "category": "sport",
                "sense_id": "sense-gentle-noun-1",
                "context": "",
                "job_status": "completed",
                "fully_complete": True,
                "missing_slots_json": "[]",
                "failure_reasons_json": "[]",
                "teenager_female_white_regular_path": valid_image.as_posix(),
                "teenager_female_white_white_bg_path": (tmp_path / "missing.jpg").as_posix(),
            }
        ],
    )

    result = service.export_job(
        job.id,
        export_fields=[
            "row_index",
            "word",
            "part_of_sentence",
            "category",
            "context",
            "job_status",
            "fully_complete",
            "missing_slots_json",
            "failure_reasons_json",
            "teenager_female_white_regular_path",
            "teenager_female_white_white_bg_path",
        ],
    )

    zip_path = Path(result["local_zip_path"])
    with zipfile.ZipFile(zip_path) as archive:
        manifest = json.loads(archive.read("_metadata/manifest.json").decode("utf-8"))
        names = archive.namelist()
    assert any("Inventory sync skipped during export" in warning for warning in manifest["export_warnings"])
    assert any("Skipped teenager_female_white_white_bg_path" in warning for warning in manifest["export_warnings"])
    assert "images/regular/0001__gentle__noun__sense-gentle-noun-1__f_tn_w_reg.jpg" in names
    assert not any(name.endswith("f_tn_w_wbg.jpg") for name in names)


def test_google_image_safety_failure_gets_user_facing_summary() -> None:
    response_json = {
        "candidates": [
            {
                "finishReason": "IMAGE_SAFETY",
                "finishMessage": "Unable to show the generated image. The image was filtered out because it violated Google's policy.",
            }
        ]
    }
    moderation = _extract_google_image_safety_details(response_json)
    assert moderation["finish_reason"] == "IMAGE_SAFETY"
    summary = _friendly_variant_error_summary(
        "female:teenager:white",
        "variant generation failed for female_teenager_white: failed",
        response_json,
    )
    assert summary == "Blocked by image safety policy for female_teenager_white"


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
    )
    item = repo.update_csv_job_item(item, shadow_run_id=shadow_run.id)
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
    )
    base_task = repo.update_csv_task(
        base_task,
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
            self.google_images = type("GoogleImages", (), {"close": lambda self: None})()

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


def test_job_overview_is_read_only_for_completed_base_shadow_run(db_session) -> None:
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
    )
    item = repo.update_csv_job_item(item, shadow_run_id=shadow_run.id, status="running")
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
    assert refreshed_task.status == "running"
    assert refreshed_task.regular_asset_id is None
    assert refreshed_task.white_bg_asset_id is None
    assert refreshed_item is not None
    assert refreshed_item.status == "running"
    assert refreshed_item.base_regular_asset_id is None
    assert refreshed_item.base_soften_asset_id is None
    assert refreshed_item.base_white_bg_asset_id is None
    assert refreshed_job is not None
    assert refreshed_job.status == "imported"


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
