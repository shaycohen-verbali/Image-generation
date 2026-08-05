from __future__ import annotations

import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine, func, select, text

from app.inventory_models import inventory_metadata, inventory_slot_column_name, word_inventory
from app.models import CsvJobItem, CsvTaskNode, Entry
from app.services.pipeline import PipelineRunner
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
    sense_wordnet: str | None = "maintain a steady position",
    sense_oxford: str | None = "a state in which weight is evenly distributed",
    synonyms: list[str] | None = None,
) -> None:
    now = datetime.utcnow()
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS aac_word_lookup ("
                "source_sense_id TEXT PRIMARY KEY, synonyms JSON NOT NULL DEFAULT '[]')"
            )
        )
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
                sense_wordnet=sense_wordnet,
                sense_oxford=sense_oxford,
                category="actions",
                context="standing steadily",
                job_status="approved",
                has_person="yes",
                image_score=88.5,
                needs_person_attention=True,
                fully_complete=False,
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            text(
                "INSERT OR REPLACE INTO aac_word_lookup (source_sense_id, synonyms) "
                "VALUES (:sense_id, :synonyms)"
            ),
            {"sense_id": sense_id, "synonyms": json.dumps(synonyms or ["equilibrium", "stability"])},
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
    assert listed["rows"][0]["image_score"] == 88.5
    assert listed["rows"][0]["needs_person_attention"] is True

    rows = source.get_rows(
        "word_inventory",
        selection_mode="single",
        row_id="inv_source_1",
    )
    assert rows[0]["_word_source_sense_id"] == "sense-balance-verb-1"
    assert rows[0]["sense_id"] == "sense-balance-verb-1"
    assert rows[0]["category"] == "maintain a steady position"
    assert rows[0]["context"] == "this word is for an AAC word board"
    assert rows[0]["word_synonyms_for_better_meaning"] == "equilibrium, stability"
    result = CsvDagService(db_session).import_word_source_rows(
        table_name="word_inventory",
        rows=rows,
        person_gender_options=["male"],
        person_age_options=["kid"],
        person_skin_color_options=["white"],
    )
    assert result["imported_count"] == 1
    entry = db_session.get(Entry, result["rows"][0]["entry_id"])
    assert entry is not None
    assert entry.sense_id == "sense-balance-verb-1"
    slug = PipelineRunner._entry_slug(entry)
    assert "sense-balance-verb-1" in slug
    assert "maintain_a_steady_position" not in slug

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


def test_export_rows_support_last_job_range_and_exact_word_pos(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    inventory_metadata.create_all(bind=engine)
    _seed_inventory_row(engine, "inv_1", word="apple", part_of_speech="noun", sense_id="sense-1")
    _seed_inventory_row(engine, "inv_2", word="balance", part_of_speech="verb", sense_id="sense-2")

    import app.services.word_sources as word_sources_module

    monkeypatch.setattr(word_sources_module, "inventory_engine", engine)
    source = WordSourceService()
    last_job = source.get_export_rows("word_inventory", selection_mode="last_job")
    exact = source.get_export_rows("word_inventory", selection_mode="single", row_id="inv_2")
    ranged = source.get_export_rows("word_inventory", selection_mode="range", range_start=1, range_end=1)

    assert [row["word"] for row in last_job] == ["apple", "balance"]
    assert exact[0]["part_of_sentence"] == "verb"
    assert exact[0]["_word_source_row_id"] == "inv_2"
    assert [row["word"] for row in ranged] == ["apple"]


def test_matalk_export_can_include_inactive_dictionary_rows(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    inventory_metadata.create_all(bind=engine)
    _seed_inventory_row(engine, "inv_inactive", word="quiet", sense_id="sense-inactive")
    with engine.begin() as conn:
        conn.execute(word_inventory.update().where(word_inventory.c.id == "inv_inactive").values(is_active=False))

    import app.services.word_sources as word_sources_module

    monkeypatch.setattr(word_sources_module, "inventory_engine", engine)
    source = WordSourceService()

    assert source.get_export_rows("word_inventory", selection_mode="all") == []
    matalk_rows = source.get_export_rows("word_inventory", selection_mode="all", include_inactive=True)
    assert len(matalk_rows) == 1
    assert matalk_rows[0]["is_active"] is False


def test_word_source_export_job_is_completed_without_runnable_items(db_session) -> None:
    result = CsvDagService(db_session).create_word_source_export_job(table_name="word_inventory")
    job = CsvDagService(db_session).repo.get_csv_job(result["job_id"])

    assert job is not None
    assert job.status == "completed"
    assert db_session.query(CsvJobItem).filter(CsvJobItem.csv_job_id == job.id).count() == 0


def test_preview_serializes_missing_senses_as_blank_strings(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    inventory_metadata.create_all(bind=engine)
    _seed_inventory_row(
        engine,
        sense_wordnet=None,
        sense_oxford=None,
    )

    import app.services.word_sources as word_sources_module

    monkeypatch.setattr(word_sources_module, "inventory_engine", engine)
    preview = WordSourceService().list_rows("word_inventory", selection_mode="all")
    assert preview["rows"][0]["sense_wordnet"] == ""
    assert preview["rows"][0]["sense_oxford"] == ""


def test_word_sense_falls_back_to_oxford_then_blank(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    inventory_metadata.create_all(bind=engine)
    _seed_inventory_row(
        engine,
        "inv_oxford",
        word="calm",
        part_of_speech="adjective",
        sense_id="sense-oxford",
        sense_wordnet="",
        sense_oxford="not showing nervousness or strong emotion",
    )
    _seed_inventory_row(
        engine,
        "inv_blank",
        word="placeholder",
        part_of_speech="noun",
        sense_id="sense-blank",
        sense_wordnet="",
        sense_oxford="",
    )

    import app.services.word_sources as word_sources_module

    monkeypatch.setattr(word_sources_module, "inventory_engine", engine)
    source = WordSourceService()
    oxford = source.get_rows("word_inventory", selection_mode="single", row_id="inv_oxford")
    blank = source.get_rows("word_inventory", selection_mode="single", row_id="inv_blank")
    assert oxford[0]["category"] == "not showing nervousness or strong emotion"
    assert blank[0]["category"] == ""


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
    monkeypatch.setattr(
        InventorySyncService,
        "slot_path_for_entry_profile",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("range import performed an N+1 inventory lookup")),
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
