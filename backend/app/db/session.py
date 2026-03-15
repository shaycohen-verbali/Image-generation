from collections.abc import Generator

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.engine_factory import create_app_engine

settings = get_settings()

engine = create_app_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
