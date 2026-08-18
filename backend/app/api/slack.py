from __future__ import annotations

import json
import logging
from urllib.parse import parse_qs

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.orm import Session

from app.api.deps import db_dependency
from app.db.session import SessionLocal
from app.services.http_client import get_http_session
from app.services.slack_service import SlackService

router = APIRouter(prefix="/api/v1/slack", tags=["slack"])

logger = logging.getLogger(__name__)

# These commands are deliberately DB-only and should answer within Slack's
# three-second window even when the background worker is stopped. Large
# inventory imports keep the response_url/background path below.
FAST_CONTROL_COMMANDS = {
    "help", "commands", "health", "status", "last", "latest", "active",
    "start", "stop", "cancel", "retry",
}


def _verify_slack_request(service: SlackService, request: Request, body: bytes) -> None:
    timestamp = str(request.headers.get("x-slack-request-timestamp") or "")
    signature = str(request.headers.get("x-slack-signature") or "")
    if not service.verify_signature(body=body, timestamp=timestamp, signature=signature):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")


def _deliver_command_reply(*, text: str, user_id: str, base_url: str, response_url: str) -> None:
    """Answer a slash command after the ack, over Slack's response_url.

    Runs outside the request, so it opens its own session rather than reusing
    the request-scoped one, which is already closed by now.
    """
    try:
        with SessionLocal() as db:
            payload = SlackService(db).slash_response(text, user_id=user_id, base_url=base_url)
    except Exception as exc:  # noqa: BLE001 - the user must hear about failures
        logger.warning("slack command failed", extra={"status": type(exc).__name__})
        payload = {"response_type": "ephemeral", "text": f"Command failed: {type(exc).__name__}"}
    try:
        get_http_session().post(response_url, json=payload, timeout=15)
    except Exception as exc:  # noqa: BLE001
        logger.warning("slack response_url delivery failed", extra={"status": type(exc).__name__})


def _deliver_dm_reply(*, text: str, user_id: str, base_url: str, channel: str) -> None:
    try:
        with SessionLocal() as db:
            service = SlackService(db)
            try:
                reply = service.dm_response_text(text, user_id=user_id, base_url=base_url)
            except Exception as exc:  # noqa: BLE001
                logger.warning("slack dm failed", extra={"status": type(exc).__name__})
                reply = f"Command failed: {type(exc).__name__}"
            service.post_message(channel=channel, text=reply)
    except Exception as exc:  # noqa: BLE001
        logger.warning("slack dm delivery failed", extra={"status": type(exc).__name__})


@router.post("/commands")
async def slack_commands(
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(db_dependency),
) -> JSONResponse:
    service = SlackService(db)
    body = await request.body()
    _verify_slack_request(service, request, body)

    form = {key: values[-1] for key, values in parse_qs(body.decode("utf-8"), keep_blank_values=True).items()}
    user_id = str(form.get("user_id") or "").strip()
    text = str(form.get("text") or "").strip()
    response_url = str(form.get("response_url") or "").strip()
    base_url = str(request.base_url).rstrip("/")

    command = str(text.split(maxsplit=1)[0] if text else "help").lower()
    # Critical control commands are intentionally answered directly from the
    # web service. The worker may be stopped or restarting; response_url work
    # queued inside that same process would make control fragile.
    if response_url and command not in FAST_CONTROL_COMMANDS:
        # Slack abandons a slash command after 3 seconds. Large `generate` and
        # export/import operations keep the durable response_url path.
        background.add_task(
            _deliver_command_reply,
            text=text,
            user_id=user_id,
            base_url=base_url,
            response_url=response_url,
        )
        return JSONResponse({"response_type": "ephemeral", "text": f"Working on `{text or 'help'}`..."})
    return JSONResponse(service.slash_response(text, user_id=user_id, base_url=base_url))


@router.post("/events")
async def slack_events(
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(db_dependency),
) -> JSONResponse:
    service = SlackService(db)
    body = await request.body()

    payload = json.loads(body.decode("utf-8") or "{}")
    if payload.get("type") == "url_verification":
        return PlainTextResponse(str(payload.get("challenge", "")))

    _verify_slack_request(service, request, body)

    event = payload.get("event") or {}
    if payload.get("type") != "event_callback":
        return JSONResponse({"ok": True})
    if request.headers.get("x-slack-retry-num"):
        return JSONResponse({"ok": True})
    if event.get("type") != "message":
        return JSONResponse({"ok": True})
    if event.get("subtype") or event.get("bot_id"):
        return JSONResponse({"ok": True})
    if str(event.get("channel_type") or "") != "im":
        return JSONResponse({"ok": True})

    user_id = str(event.get("user") or "").strip()
    channel = str(event.get("channel") or "").strip()
    text = str(event.get("text") or "").strip()
    if not channel:
        return JSONResponse({"ok": True})

    # Same 3-second rule as slash commands: a slow reply makes Slack redeliver
    # the event, and the retry guard above would then swallow the real answer.
    background.add_task(
        _deliver_dm_reply,
        text=text,
        user_id=user_id,
        base_url=str(request.base_url).rstrip("/"),
        channel=channel,
    )
    return JSONResponse({"ok": True})
