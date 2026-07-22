from __future__ import annotations

from app.api.health import healthz, livez


def test_livez_does_not_require_database() -> None:
    assert livez() == {"status": "ok"}


def test_healthz_reports_database_readiness(db_session) -> None:
    payload = healthz(db_session)

    assert payload["status"] == "ok"
    assert payload["database_status"] == "ok"
    assert payload["database_latency_ms"] >= 0
    assert payload["runs"] == 0
