from __future__ import annotations

from app.services.inventory_sync import normalize_csv_job_export_fields


def test_normalize_csv_job_export_fields_filters_invalid_and_duplicate_keys() -> None:
    normalized = normalize_csv_job_export_fields(
        ["word", "word", "teenager_female_white_regular_path", "not_real_field"]
    )

    assert normalized == ["word", "teenager_female_white_regular_path"]
