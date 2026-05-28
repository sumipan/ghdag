from __future__ import annotations

import hashlib
import inspect
import json
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ghdag.files import _rotate as _rotate_mod
from ghdag.metrics.models import FailureClass

JST = timezone(timedelta(hours=9))
_MAX_FRAMES = 5
_MAX_AUDIT_BYTES = _rotate_mod._MAX_AUDIT_BYTES


def _do_rotate(audit_path: Path) -> None:
    _rotate_mod._do_rotate(audit_path)


def _maybe_rotate(audit_path: Path) -> None:
    if not audit_path.exists():
        return
    try:
        if audit_path.stat().st_size > _MAX_AUDIT_BYTES:
            _do_rotate(audit_path)
    except OSError as e:
        print(f"[audit] warning: rotation failed: {e}", file=sys.stderr)


@dataclass
class AuditContext:
    """enqueue 経路のメタデータ。"""
    source: str = "unknown"
    correlation_id: str | None = None
    request_id: str | None = None
    parent_correlation_id: str | None = None
    orchestration_id: str | None = None


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
        "schema_version": 3,
        "timestamp": datetime.now(JST).isoformat(),
        "task_uuids": task_uuids,
        "source": context.source,
        "correlation_id": context.correlation_id,
        "request_id": context.request_id,
        "parent_correlation_id": context.parent_correlation_id,
        "orchestration_id": context.orchestration_id,
        "caller_stack": _capture_caller_stack(),
        "exec_lines_count": exec_lines_count,
        "idempotency_key": idempotency_key,
    }

    try:
        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[audit] warning: failed to write audit log: {e}", file=sys.stderr)


def compute_prompt_hash(prompt: str) -> str:
    """プロンプト文字列の SHA-256 ハッシュ先頭16文字を返す。"""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


def write_llm_inference_audit(
    audit_path: Path,
    *,
    prompt_hash: str,
    latency_ms: float,
    engine: str,
    model: str,
    correlation_id: str | None = None,
) -> None:
    """LLM 推論イベントを audit.jsonl に 1 行追記する。"""
    _maybe_rotate(audit_path)
    record = {
        "schema_version": 1,
        "event_type": "llm.inference",
        "timestamp": datetime.now(JST).isoformat(),
        "uuid": str(uuid.uuid4()),
        "prompt_hash": prompt_hash,
        "latency_ms": round(latency_ms, 1),
        "engine": engine,
        "model": model,
        "correlation_id": correlation_id,
    }
    try:
        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        print(
            f"[audit] warning: failed to write llm inference audit: {e}",
            file=sys.stderr,
        )


def write_llm_audit_log(
    audit_path: Path,
    *,
    engine: str,
    model: str,
    exit_code: int,
    correlation_id: str | None = None,
    timeout_sec: int | None = None,
    request_id: str | None = None,
) -> None:
    """llm サブコマンド用の監査ログを 1 行追記する。"""
    _maybe_rotate(audit_path)
    record = {
        "schema_version": 3,
        "event": "llm_call",
        "timestamp": datetime.now(JST).isoformat(),
        "request_id": request_id or str(uuid.uuid4()),
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
    schema_version: int = 3,
    request_id: str | None = None,
    parent_correlation_id: str | None = None,
    orchestration_id: str | None = None,
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
        "request_id": request_id,
        "parent_correlation_id": parent_correlation_id,
        "orchestration_id": orchestration_id,
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
