from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Asset


def sqlite_file_path(database_url: str) -> Path:
    if not database_url.startswith("sqlite:///"):
        raise RuntimeError("Backup helper currently supports sqlite URLs only")
    raw = database_url.removeprefix("sqlite:///")
    return Path(raw)


def backup_sqlite_database() -> Path:
    settings = get_settings()
    db_path = sqlite_file_path(settings.database_url)
    if not db_path.exists():
        raise RuntimeError(f"Database file not found: {db_path}")

    backup_root = settings.runtime_data_root / "backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = backup_root / f"aac_image_generator_{stamp}.db"
    shutil.copy2(db_path, target)
    return target


def storage_integrity_report(db: Session) -> dict[str, int]:
    assets = list(db.execute(select(Asset)).scalars())
    missing = 0
    for asset in assets:
        if not Path(asset.abs_path).exists():
            missing += 1
    return {
        "total_assets": len(assets),
        "missing_assets": missing,
    }
