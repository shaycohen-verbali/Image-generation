from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from app.api import slack as slack_api
from app.api.deps import db_dependency
from app.core.config import get_settings
from app.main import app

SIGNING_SECRET = "test-signing-secret"


def _signed_headers(body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    digest = hmac.new(
        SIGNING_SECRET.encode("utf-8"),
        f"v0:{timestamp}:{body.decode('utf-8')}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Slack-Request-Timestamp": timestamp,
        "X-Slack-Signature": f"v0={digest}",
    }


@pytest.fixture()
def client(db_session):
    """A client that does not run the app lifespan.

    Entering TestClient as a context manager fires the startup event, which
    calls init_db() against the configured database and retries for ~9s before
    giving up. These tests only exercise routing, so skip it entirely.
    """
    settings = get_settings()
    settings.slack_signing_secret = SIGNING_SECRET
    settings.slack_bot_token = "xoxb-test-token"
    settings.slack_allowed_user_ids = "U_ALLOWED"
    app.dependency_overrides[db_dependency] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_slash_command_acks_immediately_and_answers_out_of_band(client, monkeypatch) -> None:
    delivered: list[dict[str, str]] = []
    monkeypatch.setattr(
        slack_api,
        "_deliver_command_reply",
        lambda **kwargs: delivered.append(kwargs),
    )

    body = urlencode(
        {
            "command": "/verbali",
            "text": "generate range 1-1",
            "user_id": "U_ALLOWED",
            "response_url": "https://hooks.slack.com/commands/test",
        }
    ).encode("utf-8")

    response = client.post("/api/v1/slack/commands", content=body, headers=_signed_headers(body))

    assert response.status_code == 200
    assert "Working on" in response.json()["text"]
    # The real answer must be handed to the background task, not the response.
    assert len(delivered) == 1
    assert delivered[0]["text"] == "generate range 1-1"
    assert delivered[0]["response_url"] == "https://hooks.slack.com/commands/test"


def test_slash_command_answers_inline_without_a_response_url(client) -> None:
    # `help` is answered from static text, so this exercises the inline fallback
    # without the endpoint touching the thread-bound in-memory test database.
    body = urlencode({"command": "/verbali", "text": "help", "user_id": "U_ALLOWED"}).encode("utf-8")

    response = client.post("/api/v1/slack/commands", content=body, headers=_signed_headers(body))

    assert response.status_code == 200
    assert "Verbali Slack commands" in response.json()["text"]


def test_fast_control_command_answers_inline_even_with_response_url(client, monkeypatch) -> None:
    delivered: list[dict[str, str]] = []
    monkeypatch.setattr(slack_api, "_deliver_command_reply", lambda **kwargs: delivered.append(kwargs))
    monkeypatch.setattr(
        slack_api.SlackService,
        "slash_response",
        lambda self, text, *, user_id, base_url: {"response_type": "ephemeral", "text": "inline health"},
    )

    body = urlencode(
        {
            "command": "/verbali",
            "text": "health",
            "user_id": "U_ALLOWED",
            "response_url": "https://hooks.slack.com/commands/test",
        }
    ).encode("utf-8")

    response = client.post("/api/v1/slack/commands", content=body, headers=_signed_headers(body))

    assert response.status_code == 200
    assert response.json()["text"] == "inline health"
    assert delivered == []


def test_bad_signature_is_still_rejected(client) -> None:
    body = urlencode({"command": "/verbali", "text": "health"}).encode("utf-8")
    headers = _signed_headers(body) | {"X-Slack-Signature": "v0=" + "0" * 64}

    response = client.post("/api/v1/slack/commands", content=body, headers=headers)

    assert response.status_code == 401


def test_dm_event_acks_before_replying(client, monkeypatch) -> None:
    delivered: list[dict[str, str]] = []
    monkeypatch.setattr(slack_api, "_deliver_dm_reply", lambda **kwargs: delivered.append(kwargs))

    body = (
        b'{"type":"event_callback","event":{"type":"message","channel_type":"im",'
        b'"channel":"D123","user":"U_ALLOWED","text":"status"}}'
    )
    headers = _signed_headers(body) | {"Content-Type": "application/json"}

    response = client.post("/api/v1/slack/events", content=body, headers=headers)

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert len(delivered) == 1
    assert delivered[0]["channel"] == "D123"
    assert delivered[0]["text"] == "status"


def test_url_verification_still_answers_before_signature_check(client) -> None:
    response = client.post(
        "/api/v1/slack/events",
        content=b'{"type":"url_verification","challenge":"abc123"}',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    assert "abc123" in response.text
