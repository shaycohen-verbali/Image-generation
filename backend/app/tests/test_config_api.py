from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.config import router as config_router
from app.api.deps import db_dependency


def _client_with_db(db_session) -> TestClient:
    app = FastAPI()
    app.include_router(config_router)

    def override_db():
        yield db_session

    app.dependency_overrides[db_dependency] = override_db
    return TestClient(app)


def test_update_config_rejects_threshold_below_minimum(db_session) -> None:
    client = _client_with_db(db_session)
    response = client.put("/api/v1/config", json={"quality_threshold": 90})
    assert response.status_code == 422
