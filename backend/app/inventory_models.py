from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, MetaData, String, Table, Text, UniqueConstraint

AGE_VALUES = ("toddler", "kid", "tween", "teenager")
GENDER_VALUES = ("male", "female")
SKIN_VALUES = ("white", "black", "asian", "brown")
BACKGROUND_VALUES = ("regular", "white_bg")

inventory_metadata = MetaData()


def inventory_slot_column_name(age: str, gender: str, skin_color: str, background: str) -> str:
    return f"{age}_{gender}_{skin_color}_{background}_path"


slot_columns = [
    Column(inventory_slot_column_name(age, gender, skin_color, background), Text, nullable=False, default="")
    for age in AGE_VALUES
    for gender in GENDER_VALUES
    for skin_color in SKIN_VALUES
    for background in BACKGROUND_VALUES
]

word_inventory = Table(
    "word_inventory",
    inventory_metadata,
    Column("id", String(64), primary_key=True),
    Column("source_csv_job_id", String(64), nullable=False, index=True),
    Column("source_csv_job_item_id", String(64), nullable=False, index=True),
    Column("source_entry_id", String(64), nullable=False, index=True),
    Column("source_batch_id", String(64), nullable=False, index=True),
    Column("source_shadow_run_id", String(64), nullable=False, default=""),
    Column("word", String(256), nullable=False),
    Column("part_of_sentence", String(128), nullable=False),
    Column("category", String(256), nullable=False, default=""),
    Column("context", Text, nullable=False, default=""),
    Column("job_status", String(64), nullable=False, default="pending"),
    Column("fully_complete", Boolean, nullable=False, default=False),
    Column("missing_slots_json", Text, nullable=False, default="[]"),
    Column("failure_reasons_json", Text, nullable=False, default="[]"),
    Column("synced_at", DateTime, nullable=True),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    *slot_columns,
    UniqueConstraint("source_csv_job_item_id", name="uq_word_inventory_job_item"),
)
