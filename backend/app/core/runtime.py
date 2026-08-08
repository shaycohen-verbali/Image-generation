"""Process-level facts that answer "is it up, and when did it last restart?".

Render replaces the process on every deploy, crash, or restart, so the import
time of this module is the last restart time for whichever process imported it.
"""

from __future__ import annotations

from datetime import datetime

PROCESS_STARTED_AT = datetime.utcnow()


def uptime_seconds() -> int:
    return max(0, int((datetime.utcnow() - PROCESS_STARTED_AT).total_seconds()))


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    return f"{seconds // 86400}d {(seconds % 86400) // 3600}h"
