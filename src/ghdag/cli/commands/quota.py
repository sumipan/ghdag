"""ghdag quota commands."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ghdag.dag.state import load_done_from_dir
from ghdag.io import exec_jsonl
from ghdag.metrics.parsers import parse_engine_model
from ghdag.quota import QuotaGate


def cmd_quota_report(args) -> None:
    gate = _build_gate(args.state_path)
    try:
        observed_at = _parse_iso_datetime(args.observed_at, "--observed-at")
        resume_at = _parse_iso_datetime(args.resume_at, "--resume-at") if args.resume_at else None
        result = gate.report(
            engine=args.engine,
            status=args.status,
            observed_at=observed_at,
            resume_at=resume_at,
            reason=args.reason,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(_report_to_dict(result), ensure_ascii=False))


def cmd_quota_clear(args) -> None:
    gate = _build_gate(args.state_path)
    try:
        observed_at = _parse_iso_datetime(args.observed_at, "--observed-at")
        result = gate.clear(engine=args.engine, observed_at=observed_at)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(_report_to_dict(result), ensure_ascii=False))


def cmd_quota_drain(args) -> None:
    gate = _build_gate(args.state_path)
    try:
        gate.drain(engine=args.engine, reason=args.reason, now=datetime.now(timezone.utc))
        snapshot = gate.snapshot()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(_engine_status_payload(snapshot, args.engine), ensure_ascii=False))


def cmd_quota_resume(args) -> None:
    gate = _build_gate(args.state_path)
    try:
        released = gate.resume(engine=args.engine, now=datetime.now(timezone.utc))
        snapshot = gate.snapshot()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    payload = _engine_status_payload(snapshot, args.engine)
    payload["released"] = released
    print(json.dumps(payload, ensure_ascii=False))


def cmd_quota_status(args) -> None:
    gate = _build_gate(args.state_path)
    try:
        snapshot = gate.snapshot()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    deferred_by_engine: dict[str, int] = {}
    for deferred_state in snapshot.deferred_tasks.values():
        deferred_by_engine[deferred_state.engine] = (
            deferred_by_engine.get(deferred_state.engine, 0) + 1
        )

    running_by_engine: dict[str, int] = {}
    for running_state in snapshot.running_tasks.values():
        running_by_engine[running_state.engine] = (
            running_by_engine.get(running_state.engine, 0) + 1
        )

    queued_by_engine, unresolved = _collect_queued_counts(
        exec_path=Path(args.exec_path),
        done_dir=Path(args.done_dir),
        deferred_task_ids=set(snapshot.deferred_tasks.keys()),
        running_task_ids=set(snapshot.running_tasks.keys()),
    )

    engine_names = set(snapshot.engines.keys())
    engine_names.update(snapshot.draining_engines.keys())
    engine_names.update(deferred_by_engine.keys())
    engine_names.update(running_by_engine.keys())
    engine_names.update(queued_by_engine.keys())

    payload: dict[str, Any] = {"engines": {}, "unresolved": unresolved}
    for name in sorted(engine_names):
        quota_state = snapshot.engines.get(name)
        payload["engines"][name] = {
            "quota_status": quota_state.status if quota_state else "available",
            "draining": name in snapshot.draining_engines,
            "queued": queued_by_engine.get(name, 0),
            "deferred": deferred_by_engine.get(name, 0),
            "running": running_by_engine.get(name, 0),
            "idle": running_by_engine.get(name, 0) == 0,
        }
    print(json.dumps(payload, ensure_ascii=False))


def _build_gate(state_path: str) -> QuotaGate:
    path = Path(state_path)
    return QuotaGate(path, audit_path=path.parent / "audit.jsonl")


def _parse_iso_datetime(raw: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid {field_name}: {raw}") from exc
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed


def _report_to_dict(result) -> dict:
    return {
        "engine": result.engine,
        "status": result.status,
        "applied": result.applied,
        "stale_ignored": result.stale_ignored,
        "observed_at": result.observed_at.isoformat(),
        "resume_at": result.resume_at.isoformat() if result.resume_at else None,
        "reason": result.reason,
    }


def _engine_status_payload(snapshot, engine: str) -> dict:
    quota_state = snapshot.engines.get(engine)
    return {
        "engine": engine,
        "quota_status": quota_state.status if quota_state else "available",
        "draining": engine in snapshot.draining_engines,
        "deferred": sum(1 for state in snapshot.deferred_tasks.values() if state.engine == engine),
        "running": sum(1 for state in snapshot.running_tasks.values() if state.engine == engine),
        "idle": not any(state.engine == engine for state in snapshot.running_tasks.values()),
    }


def _collect_queued_counts(
    *,
    exec_path: Path,
    done_dir: Path,
    deferred_task_ids: set[str],
    running_task_ids: set[str],
) -> tuple[dict[str, int], int]:
    if not exec_path.exists():
        return {}, 0

    try:
        text = exec_jsonl.read(exec_path)
        tasks = exec_jsonl.parse(text)
    except FileNotFoundError:
        return {}, 0

    done_ids = load_done_from_dir(done_dir) if done_dir.exists() else set()
    queued_by_engine: dict[str, int] = {}
    unresolved = 0
    for task in tasks:
        if task.uuid in done_ids or task.uuid in deferred_task_ids or task.uuid in running_task_ids:
            continue
        engine = task.engine
        if engine is None:
            engine, _ = parse_engine_model(task.command)
        if engine is None:
            unresolved += 1
            continue
        queued_by_engine[engine] = queued_by_engine.get(engine, 0) + 1
    return queued_by_engine, unresolved
