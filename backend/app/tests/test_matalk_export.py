from __future__ import annotations

import csv
import json
import zipfile
from io import StringIO

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


def test_matalk_files_are_added_to_the_package_in_import_order(db_session) -> None:
    service = CsvDagService(db_session)
    export_job = service.create_word_source_export_job(table_name="word_inventory")
    result = service.export_job(
        export_job["job_id"],
        inventory_rows_override=[_person_row()],
        convert_to_matalk_tables_format=True,
    )

    with zipfile.ZipFile(result["local_zip_path"]) as archive:
        names = archive.namelist()
        dictionary = list(csv.DictReader(StringIO(archive.read("matalk/aac_dictionary.csv").decode("utf-8"))))
        meta = list(csv.DictReader(StringIO(archive.read("matalk/aac_image_meta.csv").decode("utf-8"))))
        images = list(csv.DictReader(StringIO(archive.read("matalk/aac_images.csv").decode("utf-8"))))
        manifest = json.loads(archive.read("matalk/matalk_manifest.json").decode("utf-8"))

    assert names.index("matalk/aac_dictionary.csv") < names.index("matalk/aac_image_meta.csv")
    assert names.index("matalk/aac_image_meta.csv") < names.index("matalk/aac_images.csv")
    assert dictionary[0]["source_sense_id"] == "000224222cc36d86"
    assert meta[0]["sense_id"] == "000224222cc36d86"
    assert len(images) == 2
    assert manifest["row_counts"]["aac_dictionary"] == 1
    assert manifest["row_counts"]["aac_image_meta"] == 1
    assert manifest["row_counts"]["aac_images"] == 2
    assert manifest["warnings"] == []
