from __future__ import annotations

from datetime import datetime, timedelta

from app.core.config import get_settings
from app.models import CsvJob, NotificationLog
from app.services.repository import Repository
from app.services.slack_alerts import SlackAlertService
from app.services.slack_service import SlackService


def _configure(*, allowed: str = "U_ALLOWED", alert_user: str = "") -> None:
    settings = get_settings()
    settings.slack_signing_secret = "test-signing-secret"
    settings.slack_bot_token = "xoxb-test-token"
    settings.slack_allowed_user_ids = allowed
    settings.slack_alert_user_id = alert_user


def _ask(db_session, text: str) -> str:
    return SlackService(db_session).dm_response_text(
        text, user_id="U_ALLOWED", base_url="https://example.com"
    )


# --- worker liveness -------------------------------------------------------


def test_health_reports_a_missing_worker_heartbeat(db_session) -> None:
    _configure()
    reply = _ask(db_session, "health")
    assert "no heartbeat yet" in reply
    assert "API: ok - up" in reply


def test_health_reports_a_live_worker(db_session) -> None:
    _configure()
    Repository(db_session).record_worker_heartbeat(
        worker_id="host:1", started_at=datetime.utcnow() - timedelta(hours=2)
    )
    reply = _ask(db_session, "health")
    assert "Worker: ok" in reply
    assert "up 2h" in reply


def test_health_flags_a_dead_worker(db_session) -> None:
    _configure()
    repo = Repository(db_session)
    repo.record_worker_heartbeat(worker_id="host:1", started_at=datetime.utcnow())
    beat = repo.get_latest_worker_heartbeat()
    beat.last_seen_at = datetime.utcnow() - timedelta(minutes=30)
    db_session.commit()

    reply = _ask(db_session, "health")

    assert "NOT RESPONDING" in reply
    assert "redeploy" in reply


# --- last finished job -----------------------------------------------------


def test_last_reports_the_most_recently_finished_job(db_session) -> None:
    _configure()
    older = CsvJob(
        id="csvjob_older",
        batch_id="b_older",
        status="completed",
        finished_at=datetime.utcnow() - timedelta(hours=5),
    )
    newer = CsvJob(
        id="csvjob_newer",
        batch_id="b_newer",
        status="partial_failed",
        finished_at=datetime.utcnow() - timedelta(minutes=5),
    )
    running = CsvJob(id="csvjob_running", batch_id="b_running", status="running")
    db_session.add_all([older, newer, running])
    db_session.commit()

    reply = _ask(db_session, "last")

    # `status` would answer with the running job; `last` must ignore it.
    assert "csvjob_newer" in reply
    assert "csvjob_running" not in reply


def test_last_is_explicit_when_nothing_has_finished(db_session) -> None:
    _configure()
    db_session.add(CsvJob(id="csvjob_running", batch_id="b_running", status="running"))
    db_session.commit()
    assert "No job has finished yet." in _ask(db_session, "last")


# --- generate --------------------------------------------------------------


def test_generate_without_arguments_explains_itself(db_session) -> None:
    _configure()
    reply = _ask(db_session, "generate")
    assert "generate range 1-50" in reply


def test_generate_rejects_a_malformed_range(db_session) -> None:
    _configure()
    assert "Usage: `generate range 1-50`" in _ask(db_session, "generate range fifty")


def test_generate_rejects_a_backwards_range(db_session) -> None:
    _configure()
    assert "cannot be smaller" in _ask(db_session, "generate range 50-1")


def test_generate_is_a_write_command(db_session) -> None:
    _configure(allowed="")
    reply = _ask(db_session, "generate range 1-5")
    assert "SLACK_ALLOWED_USER_IDS" in reply


def test_generate_asks_for_confirmation_above_the_threshold(db_session, monkeypatch) -> None:
    _configure()
    import app.services.word_sources as word_sources

    monkeypatch.setattr(
        word_sources.WordSourceService,
        "get_rows",
        lambda self, table, **kwargs: [{"word": f"w{i}"} for i in range(120)],
    )
    imported: list[dict] = []
    monkeypatch.setattr(
        "app.services.csv_dag_service.CsvDagService.import_word_source_rows",
        lambda self, **kwargs: imported.append(kwargs) or {"job_id": "csvjob_x"},
    )

    reply = _ask(db_session, "generate all")

    assert "120 words" in reply
    assert "confirm" in reply
    # Nothing may be imported or started until the user confirms.
    assert imported == []


def test_generate_starts_a_small_selection_without_confirmation(db_session, monkeypatch) -> None:
    _configure()
    import app.services.word_sources as word_sources

    monkeypatch.setattr(
        word_sources.WordSourceService,
        "get_rows",
        lambda self, table, **kwargs: [{"word": "apple"}, {"word": "ball"}],
    )
    monkeypatch.setattr(
        "app.services.csv_dag_service.CsvDagService.import_word_source_rows",
        lambda self, **kwargs: {"job_id": "csvjob_new", "imported_count": 2, "skipped_count": 0},
    )
    db_session.add(CsvJob(id="csvjob_new", batch_id="b_new", status="imported"))
    db_session.commit()

    reply = _ask(db_session, "generate range 1-2 gender=male age=kid")

    assert "Started `csvjob_new`" in reply
    assert "Imported 2 words" in reply
    assert "Variants: male, kid" in reply


# --- alerts ----------------------------------------------------------------


def test_alerts_are_disabled_without_an_alert_user(db_session) -> None:
    _configure(alert_user="")
    assert SlackAlertService(db_session).enabled() is False


def test_job_finished_alert_is_sent_once(db_session, monkeypatch) -> None:
    _configure(alert_user="U_ALERT")
    db_session.add(
        CsvJob(id="csvjob_done", batch_id="b_done", status="completed", finished_at=datetime.utcnow())
    )
    db_session.commit()

    sent: list[str] = []
    monkeypatch.setattr(SlackAlertService, "_send", lambda self, text: sent.append(text))
    service = SlackAlertService(db_session)

    assert service.notify_job_finished("csvjob_done") is True
    # A worker restart re-processes tasks; the claim must stop a second DM.
    assert service.notify_job_finished("csvjob_done") is False
    assert len(sent) == 1
    assert "Batch finished" in sent[0]


def test_no_alert_for_a_job_still_running(db_session, monkeypatch) -> None:
    _configure(alert_user="U_ALERT")
    db_session.add(CsvJob(id="csvjob_running", batch_id="b_running", status="running"))
    db_session.commit()

    sent: list[str] = []
    monkeypatch.setattr(SlackAlertService, "_send", lambda self, text: sent.append(text))

    assert SlackAlertService(db_session).notify_job_finished("csvjob_running") is False
    assert sent == []
    assert db_session.query(NotificationLog).count() == 0


def test_claim_notification_is_idempotent(db_session) -> None:
    repo = Repository(db_session)
    assert repo.claim_notification(kind="k", subject_id="s") is True
    assert repo.claim_notification(kind="k", subject_id="s") is False
    assert repo.claim_notification(kind="k", subject_id="other") is True
