from __future__ import annotations

import json

from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.services.maintenance import backup_sqlite_database, storage_integrity_report


if __name__ == "__main__":
    init_db()
    backup_path = backup_sqlite_database()

    with SessionLocal() as db:
        report = storage_integrity_report(db)

    print(json.dumps({"backup_path": backup_path.as_posix(), "integrity": report}, indent=2))
