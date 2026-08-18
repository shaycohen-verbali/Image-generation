import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in (
            "run_id", "stage_name", "latency_ms", "provider", "status", "cost_estimate",
            "queued_tasks", "running_tasks", "failed_tasks", "stale_tasks",
            "oldest_running_age_seconds", "worker_heartbeat_age_seconds", "query_ms",
            "process_role", "requested_parallelism", "hard_max_parallel", "effective_parallelism",
            "claiming_enabled", "worker_id", "signal", "shutdown_grace_seconds", "unfinished_ids",
            "removed_cache_files", "removed_cache_bytes", "cache_files", "cache_bytes",
            "storage_bucket", "storage_object_key",
            "cloudflare_batch_id", "cloudflare_bucket", "cloudflare_key",
            "cloudflare_response", "cloudflare_error",
        ):
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True, default=str)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
