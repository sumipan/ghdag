from __future__ import annotations

import inspect
import json
import re
import sys
import uuid
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


def _extract_task_uuids(exec_lines: list[str]) -> list[str]:
    uuids: list[str] = []
    for line in exec_lines:
        stripped = line.strip()
        if stripped.startswith("{"):
            try:
                obj = json.loads(stripped)
                if "uuid" in obj:
                    uuids.append(obj["uuid"])
                    continue
            except (json.JSONDecodeError, TypeError):
                pass
        m = _UUID_RE.match(stripped)
        if m:
            uuids.append(m.group(1))
    return uuids


def write_audit_log(
    audit_path: Path,
    exec_lines: list[str],
    context: AuditContext,
    idempotency_key: str | None = None,
) -> None:
    if not exec_lines:
        return

    task_uuids = _extract_task_uuids(exec_lines)

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


def write_llm_audit_log(
    audit_path: Path,
    *,
    engine: str,
    model: str,
    exit_code: int,
    correlation_id: str | None = None,
    timeout_sec: int | None = None,
) -> None:
    """llm サブコマンド用の監査ログを 1 行追記する。"""
    record = {
        "event": "llm_call",
        "timestamp": datetime.now(JST).isoformat(),
        "request_id": str(uuid.uuid4()),
        "source": "llm_cli",
        "correlation_id": correlation_id,
        "engine": engine,
        "model": model,
        "exit_code": exit_code,
        "timeout_sec": timeout_sec,
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
