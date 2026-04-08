from __future__ import annotations

import json
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.orm import Session

from app.api.deps import db_dependency
from app.services.slack_service import SlackService

router = APIRouter(prefix="/api/v1/slack", tags=["slack"])


def _verify_slack_request(service: SlackService, request: Request, body: bytes) -> None:
    timestamp = str(request.headers.get("x-slack-request-timestamp") or "")
    signature = str(request.headers.get("x-slack-signature") or "")
    if not service.verify_signature(body=body, timestamp=timestamp, signature=signature):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")


@router.post("/commands")
async def slack_commands(request: Request, db: Session = Depends(db_dependency)) -> JSONResponse:
    service = SlackService(db)
    body = await request.body()
    _verify_slack_request(service, request, body)

    form = {key: values[-1] for key, values in parse_qs(body.decode("utf-8"), keep_blank_values=True).items()}
    user_id = str(form.get("user_id") or "").strip()
    text = str(form.get("text") or "").strip()
    base_url = str(request.base_url).rstrip("/")
    return JSONResponse(service.slash_response(text, user_id=user_id, base_url=base_url))


@router.post("/events")
async def slack_events(request: Request, db: Session = Depends(db_dependency)) -> JSONResponse:
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

    base_url = str(request.base_url).rstrip("/")
    reply = service.dm_response_text(text, user_id=user_id, base_url=base_url)
    service.post_message(channel=channel, text=reply)
    return JSONResponse({"ok": True})
