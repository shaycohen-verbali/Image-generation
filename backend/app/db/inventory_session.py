from __future__ import annotations

from sqlalchemy import create_engine

from app.core.config import get_settings
from app.inventory_models import inventory_metadata

settings = get_settings()

inventory_engine = None
if str(settings.inventory_database_url or "").strip():
    inventory_engine = create_engine(
        settings.inventory_database_url,
        connect_args={"check_same_thread": False} if settings.inventory_database_url.startswith("sqlite") else {},
        future=True,
    )


def inventory_enabled() -> bool:
    return inventory_engine is not None


def init_inventory_db() -> None:
    if inventory_engine is None:
        return
    inventory_metadata.create_all(bind=inventory_engine)
