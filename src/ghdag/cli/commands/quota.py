"""ghdag quota commands."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

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


def cmd_quota_status(args) -> None:
    gate = _build_gate(args.state_path)
    try:
        snapshot = gate.snapshot()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    payload = {
        "engines": {
            name: {
                "status": state.status,
                "observed_at": state.observed_at.isoformat(),
                "resume_at": state.resume_at.isoformat() if state.resume_at else None,
                "reason": state.reason,
            }
            for name, state in snapshot.engines.items()
        },
        "deferred_tasks": {
            task_uuid: {
                "engine": state.engine,
                "phase": state.phase,
                "deferred_at": state.deferred_at.isoformat(),
                "reason": state.reason,
            }
            for task_uuid, state in snapshot.deferred_tasks.items()
        },
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
