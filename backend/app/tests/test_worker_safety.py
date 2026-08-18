from __future__ import annotations

import signal

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app import worker


def test_process_role_rejects_unknown_values() -> None:
    with pytest.raises(ValidationError, match="PROCESS_ROLE"):
        Settings(PROCESS_ROLE="api")


def test_effective_parallelism_never_exceeds_hard_ceiling() -> None:
    assert worker._effective_parallelism(10, 2) == 2
    assert worker._effective_parallelism(1, 2) == 1
    assert worker._effective_parallelism(0, 2) == 1


def test_claiming_is_disabled_during_cutover_or_shutdown() -> None:
    worker.SHUTDOWN_REQUESTED.clear()
    assert worker._claims_allowed(False) is False
    assert worker._claims_allowed(True) is True
    worker.SHUTDOWN_REQUESTED.set()
    assert worker._claims_allowed(True) is False
    worker.SHUTDOWN_REQUESTED.clear()


def test_shutdown_signal_is_idempotent_and_records_start(monkeypatch) -> None:
    worker.SHUTDOWN_REQUESTED.clear()
    monkeypatch.setattr(worker, "_SHUTDOWN_STARTED_AT", None)

    worker._handle_shutdown_signal(signal.SIGTERM, None)
    first_started_at = worker._SHUTDOWN_STARTED_AT
    worker._handle_shutdown_signal(signal.SIGINT, None)

    assert worker.SHUTDOWN_REQUESTED.is_set()
    assert first_started_at is not None
    assert worker._SHUTDOWN_STARTED_AT == first_started_at
    worker.SHUTDOWN_REQUESTED.clear()
    monkeypatch.setattr(worker, "_SHUTDOWN_STARTED_AT", None)
