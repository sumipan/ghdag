"""ghdag quota commands."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from ghdag.config.env import resolve_runtime_paths
from ghdag.config.env import state_dir as resolve_state_dir
from ghdag.core.exceptions import GhdagError
from ghdag.io import exec_jsonl
from ghdag.io.done import load_done_from_dir
from ghdag.llm.quota_probe import probe_quota_windows
from ghdag.metrics.parsers import parse_engine_model
from ghdag.quota import EngineUsage, QuotaGate


def cmd_quota_report(args) -> None:
    gate = _build_gate(args.state_dir)
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
        raise GhdagError(str(exc), code="QUOTA_INVALID_INPUT") from exc
    print(json.dumps(_report_to_dict(result), ensure_ascii=False))


def cmd_quota_clear(args) -> None:
    gate = _build_gate(args.state_dir)
    try:
        observed_at = _parse_iso_datetime(args.observed_at, "--observed-at")
        result = gate.clear(engine=args.engine, observed_at=observed_at)
    except ValueError as exc:
        raise GhdagError(str(exc), code="QUOTA_INVALID_INPUT") from exc
    print(json.dumps(_report_to_dict(result), ensure_ascii=False))


def cmd_quota_drain(args) -> None:
    gate = _build_gate(args.state_dir)
    try:
        gate.drain(engine=args.engine, reason=args.reason, now=datetime.now(timezone.utc))
        snapshot = gate.snapshot()
    except ValueError as exc:
        raise GhdagError(str(exc), code="QUOTA_INVALID_INPUT") from exc

    print(json.dumps(_engine_status_payload(snapshot, args.engine), ensure_ascii=False))


def cmd_quota_resume(args) -> None:
    gate = _build_gate(args.state_dir)
    try:
        released = gate.resume(engine=args.engine, now=datetime.now(timezone.utc))
        snapshot = gate.snapshot()
    except ValueError as exc:
        raise GhdagError(str(exc), code="QUOTA_INVALID_INPUT") from exc

    payload = _engine_status_payload(snapshot, args.engine)
    payload["released"] = released
    print(json.dumps(payload, ensure_ascii=False))


def cmd_quota_probe(args) -> None:
    result = probe_quota_windows(
        args.engine,
        model=args.model,
        timeout=args.timeout,
    )

    if not result.windows:
        payload = {
            "engine": result.engine,
            "model": result.model,
            "recorded": False,
            "observed_at": None,
            "returncode": result.returncode,
            "usage": None,
        }
        print(json.dumps(payload, ensure_ascii=False))
        for line in reversed(result.stderr.splitlines()):
            stripped = line.strip()
            if stripped:
                print(stripped, file=sys.stderr)
                break
        sys.exit(1)

    gate = _build_gate(args.state_dir)
    observed_at = datetime.now(timezone.utc)
    gate.observe(
        engine=result.engine,
        windows=result.windows,
        observed_at=observed_at,
    )
    usage = EngineUsage(observed_at=observed_at, windows=result.windows)
    payload = {
        "engine": result.engine,
        "model": result.model,
        "recorded": True,
        "observed_at": observed_at.isoformat(),
        "returncode": result.returncode,
        "usage": _usage_to_json(usage),
    }
    print(json.dumps(payload, ensure_ascii=False))


def cmd_quota_status(args) -> None:
    gate = _build_gate(args.state_dir)
    try:
        snapshot = gate.snapshot()
    except ValueError as exc:
        raise GhdagError(str(exc), code="QUOTA_INVALID_INPUT") from exc

    running_by_engine: dict[str, int] = {}
    for running_state in snapshot.running_tasks.values():
        running_by_engine[running_state.engine] = (
            running_by_engine.get(running_state.engine, 0) + 1
        )

    exec_path = Path(args.exec_path)
    done_dir = (
        Path(args.done_dir)
        if args.done_dir is not None
        else resolve_runtime_paths(exec_path).done_dir
    )
    queued_by_engine, unresolved = _collect_queued_counts(
        exec_path=exec_path,
        done_dir=done_dir,
        running_task_ids=set(snapshot.running_tasks.keys()),
    )

    engine_names = set(snapshot.engines.keys())
    engine_names.update(snapshot.draining_engines.keys())
    engine_names.update(snapshot.limits.keys())
    engine_names.update(running_by_engine.keys())
    engine_names.update(queued_by_engine.keys())
    engine_names.update(snapshot.usage.keys())

    payload: dict[str, Any] = {
        "engines": {},
        "unresolved": unresolved,
    }
    for name in sorted(engine_names):
        quota_state = snapshot.engines.get(name)
        is_permanent = quota_state is not None and quota_state.status == "paused" and quota_state.resume_at is None
        engine_usage = snapshot.usage.get(name)
        payload["engines"][name] = {
            # Keep the pre-drain status contract while adding the explicit
            # quota_status name and drain/run counters.
            "status": quota_state.status if quota_state else "available",
            "observed_at": quota_state.observed_at.isoformat() if quota_state else None,
            "resume_at": (
                quota_state.resume_at.isoformat()
                if quota_state and quota_state.resume_at
                else None
            ),
            "reason": quota_state.reason if quota_state else None,
            "quota_status": quota_state.status if quota_state else "available",
            "permanent_pause": is_permanent,
            "draining": name in snapshot.draining_engines,
            "queued": queued_by_engine.get(name, 0),
            "running": running_by_engine.get(name, 0),
            "idle": running_by_engine.get(name, 0) == 0,
            "limit": snapshot.limits.get(name),
            "usage": _usage_to_json(engine_usage),
        }
    print(json.dumps(payload, ensure_ascii=False))


def _build_gate(state_dir: str | None) -> QuotaGate:
    if state_dir:
        d = Path(state_dir)
        return QuotaGate(d / "quota-gate.json", audit_path=d / "audit.jsonl")
    # Default: quota state follows GHDAG_STATE_DIR, audit stays in jobs/.
    return QuotaGate(resolve_state_dir("jobs") / "quota-gate.json", audit_path=Path("jobs") / "audit.jsonl")


def _parse_iso_datetime(raw: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid {field_name}: {raw}") from exc
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed


def _usage_to_json(usage: EngineUsage | None) -> dict | None:
    if usage is None:
        return None
    return {
        "observed_at": usage.observed_at.isoformat(),
        "windows": [
            {
                "window_minutes": window.window_minutes,
                "used_percent": window.used_percent,
                "resets_at": window.resets_at.isoformat(),
            }
            for window in usage.windows
        ],
    }


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
        "running": sum(1 for state in snapshot.running_tasks.values() if state.engine == engine),
        "idle": not any(state.engine == engine for state in snapshot.running_tasks.values()),
    }


def _collect_queued_counts(
    *,
    exec_path: Path,
    done_dir: Path,
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
        if task.uuid in done_ids or task.uuid in running_task_ids:
            continue
        engine = task.engine
        if engine is None:
            engine, _ = parse_engine_model(task.command)
        if engine is None:
            unresolved += 1
            continue
        queued_by_engine[engine] = queued_by_engine.get(engine, 0) + 1
    return queued_by_engine, unresolved


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    quota_parser = subparsers.add_parser(
        "quota", allow_abbrev=False, help="Manage quota gate state",
    )
    quota_subparsers = quota_parser.add_subparsers(title="quota subcommands")

    quota_report = quota_subparsers.add_parser(
        "report", allow_abbrev=False, help="Report engine quota state",
    )
    quota_report.add_argument("engine", help="Engine name (e.g. claude)")
    quota_report.add_argument(
        "--status",
        required=True,
        choices=["available", "paused"],
        help="Quota status",
    )
    quota_report.add_argument(
        "--observed-at",
        required=True,
        dest="observed_at",
        help="Observed time in ISO8601 with timezone",
    )
    quota_report.add_argument(
        "--resume-at",
        default=None,
        dest="resume_at",
        help="Optional resume time in ISO8601 with timezone",
    )
    quota_report.add_argument(
        "--reason",
        default=None,
        help="Optional reason",
    )
    quota_report.add_argument(
        "--state-dir",
        default=None,
        dest="state_dir",
        metavar="DIR",
        help="State directory containing quota-gate.json (default: $GHDAG_STATE_DIR or jobs)",
    )
    quota_report.set_defaults(func=cmd_quota_report)

    quota_clear = quota_subparsers.add_parser(
        "clear", allow_abbrev=False, help="Clear engine quota state",
    )
    quota_clear.add_argument("engine", help="Engine name")
    quota_clear.add_argument(
        "--observed-at",
        required=True,
        dest="observed_at",
        help="Observed time in ISO8601 with timezone",
    )
    quota_clear.add_argument(
        "--state-dir",
        default=None,
        dest="state_dir",
        metavar="DIR",
        help="State directory containing quota-gate.json (default: $GHDAG_STATE_DIR or jobs)",
    )
    quota_clear.set_defaults(func=cmd_quota_clear)

    quota_drain = quota_subparsers.add_parser(
        "drain", allow_abbrev=False, help="Start engine drain mode",
    )
    quota_drain.add_argument("engine", help="Engine name")
    quota_drain.add_argument(
        "--reason",
        default=None,
        help="Optional drain reason",
    )
    quota_drain.add_argument(
        "--state-dir",
        default=None,
        dest="state_dir",
        metavar="DIR",
        help="State directory containing quota-gate.json (default: $GHDAG_STATE_DIR or jobs)",
    )
    quota_drain.set_defaults(func=cmd_quota_drain)

    quota_resume = quota_subparsers.add_parser(
        "resume", allow_abbrev=False, help="Resume engine from drain mode",
    )
    quota_resume.add_argument("engine", help="Engine name")
    quota_resume.add_argument(
        "--state-dir",
        default=None,
        dest="state_dir",
        metavar="DIR",
        help="State directory containing quota-gate.json (default: $GHDAG_STATE_DIR or jobs)",
    )
    quota_resume.set_defaults(func=cmd_quota_resume)

    quota_status = quota_subparsers.add_parser(
        "status", allow_abbrev=False, help="Show quota state snapshot",
    )
    quota_status.add_argument(
        "--state-dir",
        default=None,
        dest="state_dir",
        metavar="DIR",
        help="State directory containing quota-gate.json (default: $GHDAG_STATE_DIR or jobs)",
    )
    quota_status.add_argument(
        "--exec-jsonl",
        default="jobs/exec.jsonl",
        dest="exec_path",
        metavar="PATH",
        help="Path to exec.jsonl",
    )
    quota_status.add_argument(
        "--done-dir",
        default=None,
        dest="done_dir",
        help="Path to done marker directory (default: $GHDAG_STATE_DIR/done or <exec.jsonl parent>/done)",
    )
    quota_status.set_defaults(func=cmd_quota_status)

    quota_probe = quota_subparsers.add_parser(
        "probe", allow_abbrev=False, help="Probe engine quota usage via a minimal LLM call",
    )
    quota_probe.add_argument("engine", help="Engine name (e.g. claude)")
    quota_probe.add_argument(
        "--model",
        default=None,
        help="Model ID (default: probe-specific model for the engine)",
    )
    quota_probe.add_argument(
        "--timeout",
        type=int,
        default=120,
        metavar="SEC",
        help="Probe timeout in seconds (default: 120)",
    )
    quota_probe.add_argument(
        "--state-dir",
        default=None,
        dest="state_dir",
        metavar="DIR",
        help="State directory containing quota-gate.json (default: $GHDAG_STATE_DIR or jobs)",
    )
    quota_probe.set_defaults(func=cmd_quota_probe)
    return cast(argparse.ArgumentParser, quota_parser)
