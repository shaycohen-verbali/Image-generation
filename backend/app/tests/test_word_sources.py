from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine, func, select

from app.inventory_models import inventory_metadata, inventory_slot_column_name, word_inventory
from app.models import CsvTaskNode
from app.services.csv_dag_service import CsvDagService
from app.services.inventory_sync import InventorySyncService
from app.services.word_sources import WordSourceService


def _seed_inventory_row(
    engine,
    row_id: str = "inv_source_1",
    *,
    word: str = "balance",
    part_of_speech: str = "verb",
    sense_id: str = "sense-balance-verb-1",
) -> None:
    now = datetime.utcnow()
    with engine.begin() as conn:
        conn.execute(
            word_inventory.insert().values(
                id=row_id,
                source_csv_job_id="source_job",
                source_csv_job_item_id=f"source_item_{row_id}",
                source_entry_id=f"source_entry_{row_id}",
                source_batch_id="source_batch",
                source_shadow_run_id="",
                word=word,
                part_of_sentence=part_of_speech,
                part_of_speech=part_of_speech,
                sense_id=sense_id,
                category="actions",
                context="standing steadily",
                job_status="approved",
                has_person="yes",
                fully_complete=False,
                created_at=now,
                updated_at=now,
            )
        )


def test_word_inventory_is_the_only_approved_source() -> None:
    _, table = WordSourceService.approved_table("word_inventory")
    assert table is word_inventory
    with pytest.raises(ValueError):
        WordSourceService.approved_table("unapproved_table")


def test_word_inventory_read_import_and_writeback_targets_same_row(db_session, monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    inventory_metadata.create_all(bind=engine)
    _seed_inventory_row(engine)

    import app.services.inventory_sync as inventory_sync_module
    import app.services.word_sources as word_sources_module

    monkeypatch.setattr(word_sources_module, "inventory_engine", engine)
    monkeypatch.setattr(inventory_sync_module, "inventory_engine", engine)

    source = WordSourceService()
    listed = source.list_rows("word_inventory", search="balance")
    assert listed["total"] == 1
    assert listed["rows"][0]["id"] == "inv_source_1"

    rows = source.get_rows(
        "word_inventory",
        selection_mode="single",
        row_id="inv_source_1",
    )
    assert rows[0]["_word_source_sense_id"] == "sense-balance-verb-1"
    result = CsvDagService(db_session).import_word_source_rows(
        table_name="word_inventory",
        rows=rows,
        person_gender_options=["male"],
        person_age_options=["kid"],
        person_skin_color_options=["white"],
    )
    assert result["imported_count"] == 1

    synced = InventorySyncService(db_session).sync_csv_job(result["job_id"])
    assert synced == 1
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(word_inventory)).scalar_one() == 1
        row = conn.execute(select(word_inventory)).mappings().one()
    assert row["id"] == "inv_source_1"
    assert row["word"] == "balance"
    assert row["source_csv_job_id"] == result["job_id"]


def test_range_and_pos_selection_use_global_stable_positions(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    inventory_metadata.create_all(bind=engine)
    _seed_inventory_row(engine, "inv_1", word="apple", part_of_speech="noun", sense_id="sense-1")
    _seed_inventory_row(engine, "inv_2", word="balance", part_of_speech="verb", sense_id="sense-2")
    _seed_inventory_row(engine, "inv_3", word="calm", part_of_speech="adjective", sense_id="sense-3")

    import app.services.word_sources as word_sources_module

    monkeypatch.setattr(word_sources_module, "inventory_engine", engine)
    source = WordSourceService()
    selected = source.get_rows(
        "word_inventory",
        selection_mode="range",
        range_start=2,
        range_end=3,
        parts_of_speech=["verb"],
    )
    assert [row["word"] for row in selected] == ["balance"]

    preview = source.list_rows(
        "word_inventory",
        selection_mode="all",
        parts_of_speech=["noun", "verb"],
    )
    assert preview["total"] == 2
    assert [row["position"] for row in preview["rows"]] == [1, 2]
    assert preview["parts_of_speech"] == ["adjective", "noun", "verb"]


def test_existing_profile_image_is_skipped_unless_override_is_enabled(db_session, monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    inventory_metadata.create_all(bind=engine)
    _seed_inventory_row(engine)
    with engine.begin() as conn:
        conn.execute(
            word_inventory.update()
            .where(word_inventory.c.id == "inv_source_1")
            .values(**{inventory_slot_column_name("kid", "male", "white", "regular"): "/existing.jpg"})
        )

    import app.services.inventory_sync as inventory_sync_module
    import app.services.word_sources as word_sources_module

    monkeypatch.setattr(word_sources_module, "inventory_engine", engine)
    monkeypatch.setattr(inventory_sync_module, "inventory_engine", engine)
    rows = WordSourceService().get_rows(
        "word_inventory",
        selection_mode="single",
        row_id="inv_source_1",
    )

    skipped = CsvDagService(db_session).import_word_source_rows(
        table_name="word_inventory",
        rows=rows,
        person_gender_options=["male"],
        person_age_options=["kid"],
        person_skin_color_options=["white"],
        override_existing_variants=False,
    )
    assert db_session.query(CsvTaskNode).filter(CsvTaskNode.csv_job_id == skipped["job_id"]).count() == 0

    overridden = CsvDagService(db_session).import_word_source_rows(
        table_name="word_inventory",
        rows=rows,
        person_gender_options=["male"],
        person_age_options=["kid"],
        person_skin_color_options=["white"],
        override_existing_variants=True,
    )
    assert db_session.query(CsvTaskNode).filter(CsvTaskNode.csv_job_id == overridden["job_id"]).count() > 0
