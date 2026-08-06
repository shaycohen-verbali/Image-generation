from __future__ import annotations

import csv
import json
import zipfile
from io import StringIO

from app.api.word_sources import _prepare_cloudflare_matalk_artifacts
from app.core.config import get_settings
from app.models import CloudUpload, CloudUploadBatch
from app.services.csv_dag_service import CsvDagService
from app.services.matalk_export import (
    MATALK_DICTIONARY_FIELDS,
    MATALK_IMAGE_META_FIELDS,
    MATALK_IMAGES_FIELDS,
    build_matalk_tables,
)


def _person_row() -> dict[str, object]:
    return {
        "word": "Eat",
        "sense_id": "000224222cc36d86",
        "canonical_sense_id": "000224222cc36d86",
        "canonical_word": "eat",
        "part_of_speech": "verb",
        "main_category": "actions",
        "fine_tune_categories": ["food", "daily life"],
        "sense_oxford": "put food into the mouth",
        "sense_wordnet": "take in solid food",
        "synonyms": '["consume", "dine"]',
        "has_person": "yes",
        "image_score": 92,
        "is_active": True,
        "kid_male_white_regular_path": "https://cdn.example.test/eat-regular.jpg",
        "kid_male_white_white_bg_path": "https://cdn.example.test/eat-white.jpg",
    }


def test_build_matalk_tables_matches_the_pdf_contract() -> None:
    tables = build_matalk_tables([_person_row()])

    assert list(tables.dictionary_rows[0]) == list(MATALK_DICTIONARY_FIELDS)
    assert tables.dictionary_rows[0]["normalized_word"] == "eat"
    assert tables.dictionary_rows[0]["source_fine_tune_categories"] == '["food","daily life"]'
    assert tables.dictionary_rows[0]["synonyms"] == '["consume","dine"]'
    assert tables.dictionary_rows[0]["lookup_status"] == "active"

    assert list(tables.image_meta_rows[0]) == list(MATALK_IMAGE_META_FIELDS)
    assert tables.image_meta_rows[0]["sense_id"] == "000224222cc36d86"
    assert tables.image_meta_rows[0]["has_person"] == "true"
    assert tables.image_meta_rows[0]["is_active"] == "true"

    assert list(tables.image_rows[0]) == list(MATALK_IMAGES_FIELDS)
    assert {
        (row["age"], row["gender"], row["skin"], row["background"])
        for row in tables.image_rows
    } == {
        ("kid", "male", "white", "regular"),
        ("kid", "male", "white", "white_bg"),
    }
    assert {row["image_url"] for row in tables.image_rows} == {
        "https://cdn.example.test/eat-regular.jpg",
        "https://cdn.example.test/eat-white.jpg",
    }
    assert not tables.warnings


def test_no_person_rows_use_none_without_creating_fake_variants() -> None:
    row = {
        **_person_row(),
        "word": "apple",
        "sense_id": "000224222cc36d87",
        "has_person": "no",
        "kid_male_white_regular_path": "https://cdn.example.test/apple-regular.jpg",
        "kid_male_white_white_bg_path": "https://cdn.example.test/apple-white.jpg",
        "toddler_female_black_regular_path": "https://cdn.example.test/apple-duplicate.jpg",
    }

    tables = build_matalk_tables([row])

    assert {
        (image["age"], image["gender"], image["skin"], image["background"])
        for image in tables.image_rows
    } == {
        ("none", "none", "none", "regular"),
        ("none", "none", "none", "white_bg"),
    }
    assert tables.skipped_image_count == 0


def test_cloudflare_remote_urls_keep_regular_and_white_background_variants_distinct(db_session, monkeypatch) -> None:
    regular_path = "/inventory/eat-regular.png"
    white_path = "/inventory/eat-white.png"
    row = {
        **_person_row(),
        "kid_male_white_regular_path": regular_path,
        "kid_male_white_white_bg_path": white_path,
    }
    db_session.add_all(
        [
            CloudUpload(
                batch_id="r2_test",
                source_path=regular_path,
                variant="kid/male/white/regular",
                bucket="matalkimages",
                object_key="word_inventory/sense/kid/male/white/regular/eat.jpg",
                status="uploaded",
            ),
            CloudUpload(
                batch_id="r2_test",
                source_path=white_path,
                variant="kid/male/white/white_background",
                bucket="matalkimages",
                object_key="word_inventory/sense/kid/male/white/white_background/eat.jpg",
                status="uploaded",
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setattr(get_settings(), "cloudflare_r2_public_base_url", "https://images.example.test")

    tables = build_matalk_tables([row], db=db_session)

    assert {image["image_url"] for image in tables.image_rows} == {
        "https://images.example.test/word_inventory/sense/kid/male/white/regular/eat.jpg",
        "https://images.example.test/word_inventory/sense/kid/male/white/white_background/eat.jpg",
    }
    assert tables.skipped_image_count == 0


def test_cloudflare_batch_prepares_remote_matalk_artifacts_after_upload(db_session, monkeypatch) -> None:
    regular_path = "/inventory/eat-regular.png"
    row = {
        **_person_row(),
        "kid_male_white_regular_path": regular_path,
        "kid_male_white_white_bg_path": "",
    }
    db_session.add(
        CloudUpload(
            batch_id="r2_batch_matalk",
            source_path=regular_path,
            variant="kid/male/white/regular",
            bucket="matalkimages",
            object_key="word_inventory/sense/kid/male/white/regular/eat.jpg",
            status="uploaded",
        )
    )
    batch = CloudUploadBatch(
        id="r2_batch_matalk",
        bucket="matalkimages",
        source_rows_json=json.dumps([row]),
        matalk_enabled=True,
        status="completed",
        total=1,
        uploaded=1,
    )
    db_session.add(batch)
    db_session.commit()
    monkeypatch.setattr(get_settings(), "cloudflare_r2_public_base_url", "https://images.example.test")

    download_urls, row_counts, warnings, paths = _prepare_cloudflare_matalk_artifacts(batch, db_session)

    assert set(download_urls) == {"aac_dictionary", "aac_image_meta", "aac_images", "manifest"}
    assert row_counts["aac_images"] == 1
    assert warnings == []
    assert paths["images"].exists()
    images = list(csv.DictReader(paths["images"].open(encoding="utf-8")))
    assert images[0]["image_url"] == "https://images.example.test/word_inventory/sense/kid/male/white/regular/eat.jpg"


def test_matalk_files_are_added_to_the_package_in_import_order(db_session, tmp_path) -> None:
    regular_path = tmp_path / "eat-regular.jpg"
    white_path = tmp_path / "eat-white.jpg"
    regular_path.write_bytes(b"eat-regular")
    white_path.write_bytes(b"eat-white")
    row = {
        **_person_row(),
        "kid_male_white_regular_path": regular_path.as_posix(),
        "kid_male_white_white_bg_path": white_path.as_posix(),
    }
    service = CsvDagService(db_session)
    export_job = service.create_word_source_export_job(table_name="word_inventory")
    result = service.export_job(
        export_job["job_id"],
        inventory_rows_override=[row],
        convert_to_matalk_tables_format=True,
    )

    with zipfile.ZipFile(result["local_zip_path"]) as archive:
        names = archive.namelist()
        dictionary = list(csv.DictReader(StringIO(archive.read("matalk/aac_dictionary.csv").decode("utf-8"))))
        meta = list(csv.DictReader(StringIO(archive.read("matalk/aac_image_meta.csv").decode("utf-8"))))
        images = list(csv.DictReader(StringIO(archive.read("matalk/aac_images.csv").decode("utf-8"))))
        manifest = json.loads(archive.read("matalk/matalk_manifest.json").decode("utf-8"))
        package_manifest = json.loads(archive.read("_metadata/manifest.json").decode("utf-8"))

    assert names.index("matalk/aac_dictionary.csv") < names.index("matalk/aac_image_meta.csv")
    assert names.index("matalk/aac_image_meta.csv") < names.index("matalk/aac_images.csv")
    assert dictionary[0]["source_sense_id"] == "000224222cc36d86"
    assert meta[0]["sense_id"] == "000224222cc36d86"
    assert len(images) == 2
    assert {row["image_url"] for row in images} == {
        "images/regular/0000__Eat__unknown-pos__000224222cc36d86__m_kd_w_reg.jpg",
        "images/white_background/0000__Eat__unknown-pos__000224222cc36d86__m_kd_w_wbg.jpg",
    }
    assert "images/regular/0000__Eat__unknown-pos__000224222cc36d86__m_kd_w_reg.jpg" in names
    assert "images/white_background/0000__Eat__unknown-pos__000224222cc36d86__m_kd_w_wbg.jpg" in names
    assert manifest["row_counts"]["aac_dictionary"] == 1
    assert manifest["row_counts"]["aac_image_meta"] == 1
    assert manifest["row_counts"]["aac_images"] == 2
    assert manifest["warnings"] == []
    assert manifest["image_reference_mode"] == "zip_relative_path"
    expected_csv_files = {
        "images.csv",
        "prompts.csv",
        "_metadata/job_summary.csv",
        "_metadata/word_inventory_legacy.csv",
        "matalk/aac_dictionary.csv",
        "matalk/aac_image_meta.csv",
        "matalk/aac_images.csv",
    }
    assert set(package_manifest["csv_files"]) == expected_csv_files
    assert expected_csv_files <= set(names)
