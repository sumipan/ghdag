from __future__ import annotations

import inspect
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
_UUID_RE = re.compile(r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
_MAX_FRAMES = 5


@dataclass
class AuditContext:
    """enqueue 経路のメタデータ。"""
    source: str = "unknown"
    correlation_id: str | None = None


def write_audit_log(
    audit_path: Path,
    exec_lines: list[str],
    context: AuditContext,
    idempotency_key: str | None = None,
) -> None:
    if not exec_lines:
        return

    task_uuids = [
        m.group(1)
        for line in exec_lines
        if (m := _UUID_RE.match(line.strip()))
    ]

    record = {
        "timestamp": datetime.now(JST).isoformat(),
        "task_uuids": task_uuids,
        "source": context.source,
        "correlation_id": context.correlation_id,
        "caller_stack": _capture_caller_stack(),
        "exec_lines_count": len(exec_lines),
        "idempotency_key": idempotency_key,
    }

    try:
        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[audit] warning: failed to write audit log: {e}", file=sys.stderr)


def _capture_caller_stack() -> list[str]:
    frames = []
    for fi in inspect.stack():
        if "/ghdag/" in fi.filename:
            continue
        frames.append(f"{fi.filename}:{fi.lineno}:{fi.function}")
        if len(frames) >= _MAX_FRAMES:
            break
    return frames
