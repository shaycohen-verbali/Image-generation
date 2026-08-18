from __future__ import annotations

from app.api.health import healthz, livez


def test_livez_does_not_require_database() -> None:
    assert livez() == {"status": "ok"}


def test_healthz_reports_database_readiness(db_session) -> None:
    payload = healthz(db_session)

    assert payload["status"] == "ok"
    assert payload["database_status"] == "ok"
    assert payload["database_latency_ms"] >= 0


def test_healthz_uses_only_a_trivial_readiness_query(db_session, monkeypatch) -> None:
    calls: list[object] = []
    original_execute = db_session.execute

    def counted_execute(statement, *args, **kwargs):
        calls.append(statement)
        return original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", counted_execute)

    payload = healthz(db_session)

    assert payload["status"] == "ok"
    assert len(calls) == 1
    assert "SELECT 1" in str(calls[0]).upper()
