from __future__ import annotations

from app.core.config import get_settings
from app.db.engine_factory import create_app_engine
from app.db.session import engine as primary_engine
from app.inventory_models import inventory_metadata

settings = get_settings()

inventory_engine = None
if str(settings.inventory_database_url or "").strip():
    if str(settings.inventory_database_url).strip() == str(settings.database_url).strip():
        inventory_engine = primary_engine
    else:
        inventory_engine = create_app_engine(settings.inventory_database_url)


def inventory_enabled() -> bool:
    return inventory_engine is not None


def init_inventory_db() -> None:
    if inventory_engine is None:
        return
    inventory_metadata.create_all(bind=inventory_engine)
