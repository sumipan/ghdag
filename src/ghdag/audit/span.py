"""Latency span writer for E2E tracing (nexus #3323 / #3301 サブ8).

Public API migrated from nexus ``tools.measurement.latency_span``.
Writes one JSON object per line to ``jobs/latency_span.jsonl`` (or ``LATENCY_SPAN_PATH``).
"""
from __future__ import annotations

import fcntl
import json
import uuid
from pathlib import Path
from typing import Any

from ghdag.config.env import latency_span_path

__all__ = [
    "EVENT_TYPE_E2E_COMPLETED",
    "EVENT_TYPE_E2E_FAILED",
    "EVENT_TYPE_LATENCY_SPAN",
    "LATENCY_SPAN_JSONL",
    "emit_span",
    "make_span_id",
]

EVENT_TYPE_LATENCY_SPAN = "latency_span"
EVENT_TYPE_E2E_COMPLETED = "slack_e2e_completed"
EVENT_TYPE_E2E_FAILED = "slack_e2e_failed"

# Default path (cwd-relative) when LATENCY_SPAN_PATH is unset — same file name/form as nexus.
LATENCY_SPAN_JSONL = Path("jobs") / "latency_span.jsonl"


def make_span_id() -> str:
    return str(uuid.uuid4()).replace("-", "")[:8]


def emit_span(
    *,
    orchestration_id: str,
    name: str,
    route: str,
    start_ts: str,
    duration_ms: int,
    status: str,
    span_id: str,
    parent_span_id: str | None,
    attributes: dict[str, Any] | None = None,
    log_path: Path | None = None,
) -> None:
    """Append a single span event to latency_span.jsonl using fcntl exclusive lock."""
    if not orchestration_id:
        raise ValueError("orchestration_id is required")
    if not start_ts:
        raise ValueError("start_ts is required")
    if duration_ms is None:
        raise ValueError("duration_ms is required")
    if not span_id:
        raise ValueError("span_id is required")

    if name == "slack_e2e" and status == "ok":
        event_type = EVENT_TYPE_E2E_COMPLETED
    elif name == "slack_e2e" and status in ("error", "timeout"):
        event_type = EVENT_TYPE_E2E_FAILED
    else:
        event_type = EVENT_TYPE_LATENCY_SPAN

    record: dict[str, Any] = {
        "event_type": event_type,
        "orchestration_id": orchestration_id,
        "trace_id": orchestration_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "name": name,
        "route": route,
        "start_ts": start_ts,
        "duration_ms": int(duration_ms),
        "status": status,
    }
    if attributes:
        record["attributes"] = attributes

    if log_path is not None:
        path = log_path
    else:
        env_path = latency_span_path()
        path = Path(env_path) if env_path else LATENCY_SPAN_JSONL
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
