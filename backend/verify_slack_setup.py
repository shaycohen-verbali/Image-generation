"""Check that a deployed backend is wired up correctly for Slack.

Run this after creating the Slack app and setting the Render environment
variables. It exercises the three things that actually break in practice:
the event challenge, a correctly signed slash command, and rejection of a
bad signature.

    export SLACK_SIGNING_SECRET=...        # same value as Render
    python verify_slack_setup.py https://your-api.onrender.com --user-id U0123456789

The signing secret is read from the environment and is never printed.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT_SECONDS = 30


def _post(url: str, body: bytes, headers: dict[str, str]) -> tuple[int, str]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        return 0, f"connection failed: {exc.reason}"


def _sign(secret: str, timestamp: str, body: bytes) -> str:
    basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
    digest = hmac.new(secret.encode("utf-8"), basestring.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"v0={digest}"


def check_event_challenge(base_url: str) -> bool:
    """The events endpoint must echo Slack's challenge before any signing."""
    expected = "verbali_setup_challenge_token"
    body = json.dumps({"type": "url_verification", "challenge": expected}).encode("utf-8")
    status, text = _post(
        f"{base_url}/api/v1/slack/events",
        body,
        {"Content-Type": "application/json"},
    )
    ok = status == 200 and expected in text
    print(f"[{'PASS' if ok else 'FAIL'}] event challenge -> HTTP {status}")
    if not ok:
        print(f"       expected the challenge echoed back, got: {text[:200]}")
    return ok


def check_signed_command(base_url: str, secret: str, user_id: str) -> bool:
    """A correctly signed slash command must be accepted and answered."""
    form = {
        "token": "setup-check",
        "team_id": "T000000",
        "channel_id": "C000000",
        "user_id": user_id,
        "user_name": "setup-check",
        "command": "/verbali",
        "text": "health",
        "response_url": "https://hooks.slack.com/commands/setup-check",
        "trigger_id": "0.0.setup-check",
    }
    body = urllib.parse.urlencode(form).encode("utf-8")
    timestamp = str(int(time.time()))
    status, text = _post(
        f"{base_url}/api/v1/slack/commands",
        body,
        {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Slack-Request-Timestamp": timestamp,
            "X-Slack-Signature": _sign(secret, timestamp, body),
        },
    )
    ok = status == 200
    print(f"[{'PASS' if ok else 'FAIL'}] signed slash command -> HTTP {status}")
    if not ok:
        print(f"       {text[:300]}")
        return False

    # A 200 proves the signature verified. The body still tells us whether the
    # backend has its Slack settings and whether this user is on the allowlist.
    if "not configured" in text:
        print("       signature OK, but SLACK_BOT_TOKEN / SLACK_SIGNING_SECRET are missing on the server")
        return False
    if "not allowed" in text:
        print(f"       signature OK, but {user_id} is not in SLACK_ALLOWED_USER_IDS")
        return False
    print(f"       {text[:300]}")
    return True


def check_bad_signature_rejected(base_url: str) -> bool:
    """An invalid signature must be refused, or the endpoint is wide open."""
    body = urllib.parse.urlencode({"command": "/verbali", "text": "health"}).encode("utf-8")
    status, _ = _post(
        f"{base_url}/api/v1/slack/commands",
        body,
        {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Slack-Request-Timestamp": str(int(time.time())),
            "X-Slack-Signature": "v0=0000000000000000000000000000000000000000000000000000000000000000",
        },
    )
    ok = status == 401
    print(f"[{'PASS' if ok else 'FAIL'}] bad signature rejected -> HTTP {status}")
    if not ok:
        print("       expected HTTP 401. Anything else means unsigned requests are being accepted.")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Slack wiring against a deployed backend")
    parser.add_argument("base_url", help="Backend base URL, for example https://your-api.onrender.com")
    parser.add_argument("--user-id", default="U000000", help="Your Slack user ID, to test the allowlist")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    secret = os.environ.get("SLACK_SIGNING_SECRET", "").strip()
    if not secret:
        print("SLACK_SIGNING_SECRET is not set in this shell. Export the same value you set in Render.")
        return 2

    print(f"Checking {base_url}\n")
    results = [
        check_event_challenge(base_url),
        check_signed_command(base_url, secret, args.user_id),
        check_bad_signature_rejected(base_url),
    ]
    print()
    if all(results):
        print("All checks passed. Slack can reach the backend and signatures verify.")
        return 0
    print("Some checks failed. See docs/slack_setup.md for the fix for each one.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
