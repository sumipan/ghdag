"""ghdag.tool.audit — Tool fallback chain audit logging."""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ghdag.files._rotate import _maybe_rotate

JST = timezone(timedelta(hours=9))


def write_tool_fallback_audit(
    audit_path: Path,
    *,
    tool: str,
    original_engine: str,
    original_model: str,
    fallback_engine: str,
    fallback_model: str,
    fallback_index: int,
    reason: str,
    correlation_id: str | None = None,
) -> None:
    """Record a tool fallback chain activation to audit.jsonl."""
    _maybe_rotate(audit_path)
    record: dict[str, object] = {
        "schema_version": 1,
        "event": "tool.fallback",
        "timestamp": datetime.now(JST).isoformat(),
        "uuid": str(uuid.uuid4()),
        "tool": tool,
        "original_engine": original_engine,
        "original_model": original_model,
        "fallback_engine": fallback_engine,
        "fallback_model": fallback_model,
        "fallback_index": fallback_index,
        "reason": reason,
    }
    if correlation_id is not None:
        record["correlation_id"] = correlation_id

    try:
        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        print(
            f"[audit] warning: failed to write tool fallback audit: {e}",
            file=sys.stderr,
        )
