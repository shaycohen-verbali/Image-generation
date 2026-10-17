from datetime import datetime

from sqlalchemy import create_engine, insert, select

from app.inventory_models import inventory_metadata, word_inventory
from app.models import Base, MatalkClassUpload
from app.services.class_categories import refresh_class_upload_categories


def _inventory_row(row_id, sense_id, main_category, active=True):
    # Required columns without defaults get a placeholder; the refresh reads
    # only sense_id, main_category and is_active.
    def placeholder(column):
        kind = column.type.python_type
        return datetime(2026, 1, 1) if kind is datetime else kind() if kind in (int, bool, float) else row_id

    row = {
        column.name: placeholder(column) for column in word_inventory.c
        if not column.nullable and column.default is None and column.server_default is None
    }
    row.update({
        "id": row_id, "word": "bat", "category": "a dictionary line", "sense_id": sense_id,
        "main_category": main_category, "is_active": active,
    })
    return row


def _upload(row_id, sense_id, category):
    return MatalkClassUpload(
        id=row_id, batch_id="b", word="bat", sense_id=sense_id, variant="kid/male/white/white_background",
        category=category, bucket="matalk-class-images", object_key=f"{row_id}.jpg", status="uploaded",
    )


def test_refresh_sets_main_category_clears_text_and_is_idempotent():
    ledger = create_engine("sqlite:///:memory:", future=True)
    inventory = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(ledger)
    inventory_metadata.create_all(inventory)
    with inventory.begin() as conn:
        for row in (
            _inventory_row("1", "bat-animal", "plants or animals"),
            _inventory_row("2", "bat-club", "object or tool"),
            _inventory_row("3", "bat-club", "", active=False),
            _inventory_row("4", "bat-wink", ""),
        ):
            conn.execute(insert(word_inventory).values(**{k: v for k, v in row.items() if k in word_inventory.c}))
    from sqlalchemy.orm import Session
    with Session(ledger) as db:
        db.add_all([
            _upload("u1", "bat-animal", "a dictionary line"),
            _upload("u2", "bat-animal", "a dictionary line"),
            _upload("u3", "bat-club", ""),
            _upload("u4", "bat-wink", "to close and open one eye"),
            _upload("u5", "not-in-inventory", "leftover text"),
        ])
        db.commit()

    assert refresh_class_upload_categories(ledger, inventory) == 5
    with ledger.connect() as conn:
        categories = dict(conn.execute(select(MatalkClassUpload.id, MatalkClassUpload.category)).all())
    assert categories == {
        "u1": "plants or animals", "u2": "plants or animals", "u3": "object or tool", "u4": "", "u5": "",
    }
    # A second startup finds nothing to change.
    assert refresh_class_upload_categories(ledger, inventory) == 0


def test_refresh_without_inventory_does_nothing():
    ledger = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(ledger)
    assert refresh_class_upload_categories(ledger, None) == 0


def test_dictionary_view_is_postgres_only():
    from app.services.class_categories import ensure_class_dictionary_view
    assert ensure_class_dictionary_view(None) is False
    assert ensure_class_dictionary_view(create_engine("sqlite:///:memory:", future=True)) is False
