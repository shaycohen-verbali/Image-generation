from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine, func, select

from app.inventory_models import inventory_metadata, word_inventory
from app.services.csv_dag_service import CsvDagService
from app.services.inventory_sync import InventorySyncService
from app.services.word_sources import WordSourceService


def _seed_inventory_row(engine, row_id: str = "inv_source_1") -> None:
    now = datetime.utcnow()
    with engine.begin() as conn:
        conn.execute(
            word_inventory.insert().values(
                id=row_id,
                source_csv_job_id="source_job",
                source_csv_job_item_id="source_item",
                source_entry_id="source_entry",
                source_batch_id="source_batch",
                source_shadow_run_id="",
                word="balance",
                part_of_sentence="verb",
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

    rows = source.get_rows("word_inventory", ["inv_source_1"])
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
