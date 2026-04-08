from __future__ import annotations

from app.core.config import get_settings
from app.models import CsvJob, Entry, Run
from app.services.slack_service import SlackService


def test_slack_service_returns_run_summary_for_allowed_user(db_session) -> None:
    settings = get_settings()
    settings.slack_signing_secret = "test-signing-secret"
    settings.slack_bot_token = "xoxb-test-token"
    settings.slack_allowed_user_ids = "U_ALLOWED"

    entry = Entry(
        id="ent_slack_1",
        word="apple",
        part_of_sentence="noun",
        category="food",
        context="A shiny apple",
        boy_or_girl="female",
        person_gender_options_json='["female"]',
        person_age_options_json='["kid"]',
        person_skin_color_options_json='["white"]',
        batch="batch_slack",
        has_person="yes",
        source_row_hash="hash_slack_1",
    )
    run = Run(
        id="run_slack_1",
        entry_id=entry.id,
        execution_mode="legacy",
        status="running",
        current_stage="stage3_upgrade",
        optimization_attempt=1,
        max_optimization_attempts=3,
        quality_threshold=95,
    )
    db_session.add_all([entry, run])
    db_session.commit()

    service = SlackService(db_session)
    text = service.dm_response_text("run run_slack_1", user_id="U_ALLOWED", base_url="https://example.com")

    assert "*Run* `run_slack_1`" in text
    assert "Word: apple" in text
    assert "Stage: stage3_upgrade" in text


def test_slack_service_blocks_unauthorized_user(db_session) -> None:
    settings = get_settings()
    settings.slack_signing_secret = "test-signing-secret"
    settings.slack_bot_token = "xoxb-test-token"
    settings.slack_allowed_user_ids = "U_ALLOWED"

    service = SlackService(db_session)
    text = service.dm_response_text("health", user_id="U_OTHER", base_url="https://example.com")

    assert "not allowed" in text.lower()


def test_slack_service_reports_csv_export_status(db_session) -> None:
    settings = get_settings()
    settings.slack_signing_secret = "test-signing-secret"
    settings.slack_bot_token = "xoxb-test-token"
    settings.slack_allowed_user_ids = "U_ALLOWED"

    job = CsvJob(
        id="csvjob_slack_1",
        batch_id="batch_slack_1",
        execution_mode="csv_dag",
        source_file_name="words.csv",
        config_snapshot_json="{}",
        status="completed",
    )
    db_session.add(job)
    db_session.commit()

    service = SlackService(db_session)
    text = service.dm_response_text("export csvjob_slack_1", user_id="U_ALLOWED", base_url="https://example.com")

    assert "*CSV export* `csvjob_slack_1`" in text
    assert "Export ready: yes" in text
