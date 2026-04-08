from __future__ import annotations

import csv
import json
from pathlib import Path

from app.models import Asset, Entry, Prompt, Run
from app.services.export_service import ExportService


def test_create_export_honors_selected_csv_fields(db_session, tmp_path: Path) -> None:
    stage3_path = tmp_path / "stage3_upgraded.jpg"
    stage4_path = tmp_path / "stage4_white_bg.jpg"
    stage3_path.write_bytes(b"stage3")
    stage4_path.write_bytes(b"stage4")

    entry = Entry(
        id="ent_export_1",
        word="apple",
        part_of_sentence="noun",
        category="food",
        context="A red apple on a table",
        boy_or_girl="female",
        person_gender_options_json='["female"]',
        person_age_options_json='["kid"]',
        person_skin_color_options_json='["white"]',
        batch="batch_export",
        has_person="yes",
        source_row_hash="hash_export_1",
    )
    run = Run(
        id="run_export_1",
        entry_id=entry.id,
        execution_mode="legacy",
        status="completed_pass",
        current_stage="completed",
        optimization_attempt=1,
        quality_score=99,
    )
    prompt = Prompt(
        run_id=run.id,
        stage_name="stage1_prompt",
        attempt=1,
        prompt_text="Draw an apple",
        needs_person="yes",
        source="stage1",
        raw_response_json=json.dumps({}),
    )
    stage3_asset = Asset(
        run_id=run.id,
        stage_name="stage3_upgraded",
        attempt=1,
        file_name="apple_stage3.jpg",
        abs_path=stage3_path.as_posix(),
        mime_type="image/jpeg",
        sha256="sha_stage3",
        width=100,
        height=100,
        origin_url="",
        model_name="test-model",
    )
    stage4_asset = Asset(
        run_id=run.id,
        stage_name="stage4_white_bg",
        attempt=1,
        file_name="apple_stage4.jpg",
        abs_path=stage4_path.as_posix(),
        mime_type="image/jpeg",
        sha256="sha_stage4",
        width=100,
        height=100,
        origin_url="",
        model_name="test-model",
    )
    db_session.add_all([entry, run, prompt, stage3_asset, stage4_asset])
    db_session.commit()

    service = ExportService(db_session)
    record = service.create_export(
        {
            "run_ids": [run.id],
            "export_fields": ["word", "image_without_background", "word", "not_a_real_field"],
        }
    )

    assert record is not None
    assert record.status == "completed"

    csv_path = Path(record.csv_path)
    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert rows
    assert rows[0] == {
        "word": "apple",
        "image without background": stage4_path.as_posix(),
    }

    filter_json = json.loads(record.filter_json)
    assert filter_json["export_fields"] == ["word", "image_without_background"]
