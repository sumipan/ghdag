from __future__ import annotations

import inspect
import json
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path

from ghdag.metrics.models import FailureClass

JST = timezone(timedelta(hours=9))
_MAX_FRAMES = 5
_MAX_AUDIT_BYTES = 64 * 1024 * 1024


def _should_rotate_daily(audit_path: Path) -> bool:
    with open(audit_path, encoding="utf-8") as f:
        first_line = f.readline().strip()
    if not first_line:
        return False
    try:
        rec = json.loads(first_line)
        ts = datetime.fromisoformat(rec["timestamp"])
        return ts.astimezone(JST).date() < datetime.now(JST).date()
    except (json.JSONDecodeError, KeyError, ValueError):
        return False


def _do_rotate(audit_path: Path) -> None:
    ts = datetime.now(JST).strftime("%Y-%m-%dT%H-%M-%S")
    rotated = audit_path.with_name(f"audit.{ts}.jsonl")
    audit_path.rename(rotated)


def _maybe_rotate(audit_path: Path) -> None:
    if not audit_path.exists():
        return
    try:
        if audit_path.stat().st_size > _MAX_AUDIT_BYTES or _should_rotate_daily(audit_path):
            _do_rotate(audit_path)
    except OSError as e:
        print(f"[audit] warning: rotation failed: {e}", file=sys.stderr)


@dataclass
class AuditContext:
    """enqueue 経路のメタデータ。"""
    source: str = "unknown"
    correlation_id: str | None = None


def write_audit_log(
    audit_path: Path,
    *,
    task_uuids: list[str],
    exec_lines_count: int,
    context: AuditContext,
    idempotency_key: str | None = None,
) -> None:
    if exec_lines_count == 0:
        return

    _maybe_rotate(audit_path)
    record = {
        "timestamp": datetime.now(JST).isoformat(),
        "task_uuids": task_uuids,
        "source": context.source,
        "correlation_id": context.correlation_id,
        "caller_stack": _capture_caller_stack(),
        "exec_lines_count": exec_lines_count,
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
    _maybe_rotate(audit_path)
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


def write_task_exit_audit(
    audit_path: Path,
    *,
    event_type: str,
    uuid: str,
    status: str,
    elapsed_sec: float | None = None,
    token_count: int | None = None,
    model: str | None = None,
    engine: str | None = None,
    correlation_id: str | None = None,
    failure_class: FailureClass | None = None,
    schema_version: int = 1,
) -> None:
    _maybe_rotate(audit_path)
    record = {
        "schema_version": schema_version,
        "event_type": event_type,
        "timestamp": datetime.now(JST).isoformat(),
        "uuid": uuid,
        "status": status,
        "failure_class": failure_class.value if failure_class else None,
        "elapsed_sec": elapsed_sec,
        "token_count": token_count,
        "model": model,
        "engine": engine,
        "correlation_id": correlation_id,
    }

    try:
        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[audit] warning: failed to write audit log: {e}", file=sys.stderr)


def write_rate_limit_audit(
    audit_path: Path,
    *,
    remaining: int,
    limit: int,
    reset: int,
    correlation_id: str | None = None,
) -> None:
    """rate limit snapshot を audit.jsonl に 1 行追記する。"""
    _maybe_rotate(audit_path)
    record = {
        "event": "github_rate_limit",
        "timestamp": datetime.now(JST).isoformat(),
        "remaining": remaining,
        "limit": limit,
        "reset": reset,
        "correlation_id": correlation_id,
    }
    try:
        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[audit] warning: failed to write rate limit audit: {e}", file=sys.stderr)


def _capture_caller_stack() -> list[str]:
    frames = []
    for fi in inspect.stack():
        if "/ghdag/" in fi.filename:
            continue
        frames.append(f"{fi.filename}:{fi.lineno}:{fi.function}")
        if len(frames) >= _MAX_FRAMES:
            break
    return frames
