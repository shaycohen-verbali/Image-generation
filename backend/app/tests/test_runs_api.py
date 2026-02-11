from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import db_dependency
from app.api.runs import router as runs_router
from app.services.repository import Repository


def _client_with_db(db_session) -> TestClient:
    app = FastAPI()
    app.include_router(runs_router)

    def override_db():
        yield db_session

    app.dependency_overrides[db_dependency] = override_db
    return TestClient(app)


def test_list_runs_includes_review_warning_fields(db_session) -> None:
    repo = Repository(db_session)
    entry = repo.create_entry(
        {
            "word": "none",
            "part_of_sentence": "pronoun",
            "category": "",
            "context": "no apples",
            "boy_or_girl": "girl",
            "batch": "1",
        }
    )
    run = repo.create_runs([entry.id], quality_threshold=90, max_optimization_attempts=3)[0]
    repo.update_run(run, review_warning=True, review_warning_reason="Abstract word failed repeatedly.")

    client = _client_with_db(db_session)
    response = client.get("/api/v1/runs")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) >= 1
    assert "review_warning" in rows[0]
    assert "review_warning_reason" in rows[0]


def test_run_detail_includes_review_warning_fields(db_session) -> None:
    repo = Repository(db_session)
    entry = repo.create_entry(
        {
            "word": "none",
            "part_of_sentence": "pronoun",
            "category": "",
            "context": "no apples",
            "boy_or_girl": "girl",
            "batch": "1",
        }
    )
    run = repo.create_runs([entry.id], quality_threshold=90, max_optimization_attempts=3)[0]
    run = repo.update_run(run, review_warning=True, review_warning_reason="Abstract word failed repeatedly.")

    client = _client_with_db(db_session)
    response = client.get(f"/api/v1/runs/{run.id}")
    assert response.status_code == 200
    payload = response.json()["run"]
    assert payload["review_warning"] is True
    assert payload["review_warning_reason"] == "Abstract word failed repeatedly."


def test_create_runs_rejects_threshold_below_minimum(db_session) -> None:
    repo = Repository(db_session)
    entry = repo.create_entry(
        {
            "word": "apple",
            "part_of_sentence": "noun",
            "category": "food",
            "context": "single apple",
            "boy_or_girl": "girl",
            "batch": "1",
        }
    )

    client = _client_with_db(db_session)
    response = client.post(
        "/api/v1/runs",
        json={
            "entry_ids": [entry.id],
            "quality_threshold": 90,
        },
    )

    assert response.status_code == 422
