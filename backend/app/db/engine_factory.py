from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool


def create_app_engine(database_url: str):
    kwargs = {
        "future": True,
        "connect_args": {"check_same_thread": False} if str(database_url).startswith("sqlite") else {},
    }
    if not str(database_url).startswith("sqlite"):
        # Supabase session pooler already manages pooling. Avoid holding extra clients open per process.
        kwargs["poolclass"] = NullPool
        kwargs["pool_pre_ping"] = True
    return create_engine(database_url, **kwargs)
