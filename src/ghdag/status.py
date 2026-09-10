"""ghdag.status — Issue × handler の現在状態を返す公開 API (nexus #3084)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ghdag.core.vocabulary import (
    DONE_CANCELLED,
    DONE_DEP_FAILED,
    DONE_SKIPPED_MISSING_INPUT,
)
from ghdag.io import audit_query, exec_jsonl
from ghdag.io.done import dep_succeeded, interpret_done, read_done_content

logger = logging.getLogger(__name__)

__all__ = [
    "IssueStatus",
    "StepStatus",
    "RunningTask",
    "issue_status",
    "running_tasks",
]

# Canonical English statuses returned by _step_status_core (pipeline maps to JP).
# issue_status() collapses terminal non-success kinds into AC-1 vocabulary.
_ISSUE_STATUS_VALUES = frozenset(
    {
        "success",
        "failed",
        "pending",
        "running",
        "skipped",
        "cancelled",
        "dep_failed",
    }
)

_COLLAPSE_TO_FAILED = frozenset(
    {
        "rejected",
        "empty_result",
        "engine_error",
        "other",
        "failed_exit",
    }
)

_TERMINAL_FAILURE = frozenset(
    {
        "failed",
        "failed_exit",
        "rejected",
        "empty_result",
        "engine_error",
        "other",
        "dep_failed",
        "cancelled",
        "skipped",
    }
)


@dataclass
class StepStatus:
    uuid: str
    step_name: str
    depends: list[str]
    status: str  # success | failed | pending | running | skipped | cancelled | dep_failed
    started_at: str | None
    elapsed_sec: float | None
    result_path: str | None


@dataclass
class IssueStatus:
    generation: int
    steps: list[StepStatus]
    running: bool
    orphan_uuids: list[str]


@dataclass
class RunningTask:
    uuid: str
    started_at: str
    elapsed_sec: float
    engine: str | None
    pid: int | None


@dataclass
class _StepRecord:
    """Internal step snapshot shared by issue_status and plan_recover."""

    uuid: str
    step_name: str
    depends: list[str]
    command: str
    result_path: str | None
    status: str  # _step_status_core vocabulary


def _first_line(raw: str) -> str:
    lines = raw.strip().splitlines()
    return lines[0].strip() if lines else ""


def _done_kind(raw: str | None) -> str | None:
    """Map done-marker body to a core status token (or None if absent)."""
    if raw is None:
        return None
    first = _first_line(raw)
    if first == DONE_CANCELLED:
        return "cancelled"
    if first == DONE_DEP_FAILED:
        return "dep_failed"
    if first == DONE_SKIPPED_MISSING_INPUT:
        return "skipped"
    kind = interpret_done(raw)
    if kind == "success":
        return "success"
    if kind == "rejected":
        return "rejected"
    if kind == "empty_result":
        return "empty_result"
    if kind == "engine_error":
        return "engine_error"
    if kind == "failed_exit":
        return "failed_exit"
    return "other"


def _dep_outcome(
    done_dir: Path,
    dep_uuid: str,
    *,
    running_uuids: set[str],
) -> str:
    """Return success | failed | pending | running for a dependency uuid."""
    if dep_uuid in running_uuids:
        return "running"
    kind = _done_kind(read_done_content(done_dir, dep_uuid))
    if kind is None:
        return "pending"
    if kind == "success":
        return "success"
    return "failed"


def _step_status_core(
    uuid: str,
    done_dir: str | Path,
    *,
    depends: list[str] | set[str] | None = None,
    running_uuids: set[str] | None = None,
    deferred_uuids: set[str] | None = None,
) -> str:
    """Canonical English step status shared by issue_status and pipeline.task_status.

    Priority:
    1. running_uuids (takes precedence over a stale done marker)
    2. done marker content
    3. deferred_uuids
    4. dependency outcomes → dep_failed | pending
    5. pending (ready / waiting without terminal dep failure)
    """
    done_path = Path(done_dir)
    running = running_uuids or set()

    if uuid in running:
        return "running"

    done_status = _done_kind(read_done_content(done_path, uuid))
    if done_status is not None:
        return done_status

    if deferred_uuids and uuid in deferred_uuids:
        return "deferred"

    dep_list = list(depends) if depends else []
    if dep_list:
        any_failed = False
        any_unsatisfied = False
        for dep in dep_list:
            outcome = _dep_outcome(done_path, str(dep), running_uuids=running)
            if outcome == "failed":
                any_failed = True
            elif outcome != "success":
                any_unsatisfied = True
        if any_failed:
            return "dep_failed"
        if any_unsatisfied:
            return "pending"

    return "pending"


def _to_issue_status_value(core_status: str) -> str:
    if core_status in _ISSUE_STATUS_VALUES:
        return core_status
    if core_status in _COLLAPSE_TO_FAILED:
        return "failed"
    if core_status == "deferred":
        return "pending"
    return "failed"


def _to_recover_status(core_status: str) -> str:
    """Coarsen core status to RecoverStepInfo vocabulary."""
    if core_status == "success":
        return "success"
    if core_status == "running":
        return "running"
    if core_status == "pending" or core_status == "deferred":
        return "pending"
    return "failed"


def _propagate_dep_failed(records: list[_StepRecord]) -> None:
    """Mark pending steps as dep_failed when any in-graph dependency is terminal."""
    by_uuid = {rec.uuid: rec for rec in records}
    changed = True
    while changed:
        changed = False
        for rec in records:
            if rec.status != "pending":
                continue
            for dep in rec.depends:
                dep_rec = by_uuid.get(dep)
                if dep_rec is not None and dep_rec.status in _TERMINAL_FAILURE:
                    rec.status = "dep_failed"
                    changed = True
                    break


def _read_step_records(
    *,
    state_dir: str | Path,
    exec_jsonl_path: str | Path,
    workflow_name: str,
    handler_name: str,
    issue_number: int,
    done_dir: str | Path,
    running_uuids: set[str] | None = None,
) -> tuple[int, str, list[_StepRecord]]:
    """Load current-generation step records and attach core status.

    Shared by ``issue_status`` and ``dag.recover.plan_recover``.
    """
    generation = exec_jsonl.get_generation(
        state_dir, workflow_name, handler_name, issue_number,
    )
    idempotency_key = exec_jsonl.build_idempotency_key(
        workflow_name, handler_name, issue_number, generation,
    )
    records = exec_jsonl.find_records_by_idempotency_key(
        Path(exec_jsonl_path), idempotency_key,
    )
    running = running_uuids or set()
    done_path = Path(done_dir)
    step_records: list[_StepRecord] = []
    for rec in records:
        uuid = str(rec["uuid"])
        annotations = rec.get("annotations") or {}
        step_name = str(annotations.get("step_name") or uuid)
        depends = [str(d) for d in rec.get("depends", [])]
        result_path = rec.get("result_path") or None
        if result_path is not None:
            result_path = str(result_path)
        status = _step_status_core(
            uuid,
            done_path,
            depends=depends,
            running_uuids=running,
        )
        step_records.append(
            _StepRecord(
                uuid=uuid,
                step_name=step_name,
                depends=depends,
                command=str(rec.get("command", "")),
                result_path=result_path,
                status=status,
            )
        )
    _propagate_dep_failed(step_records)
    return generation, idempotency_key, step_records


def _parse_started_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _elapsed_since(started_at: str, *, now: datetime | None = None) -> float | None:
    started = _parse_started_at(started_at)
    if started is None:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return max(0.0, (current - started).total_seconds())


def _read_running_payload(running_dir: Path, uuid: str) -> dict | None:
    path = running_dir / f"{uuid}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _timing_from_audit(
    audit_path: Path,
    uuid: str,
) -> tuple[str | None, float | None]:
    events = audit_query.read_task_exit_events(audit_path, uuid=uuid)
    if not events:
        return None, None
    latest = events[-1]
    elapsed = latest.get("elapsed_sec")
    elapsed_f: float | None
    if isinstance(elapsed, (int, float)):
        elapsed_f = float(elapsed)
    else:
        elapsed_f = None
    started_at: str | None = None
    # Prefer an explicit started_at if present; otherwise derive from timestamp - elapsed.
    raw_started = latest.get("started_at")
    if isinstance(raw_started, str):
        started_at = raw_started
    elif elapsed_f is not None:
        ts = latest.get("timestamp")
        if isinstance(ts, str):
            ended = _parse_started_at(ts)
            if ended is not None:
                if ended.tzinfo is None:
                    ended = ended.replace(tzinfo=timezone.utc)
                started_at = datetime.fromtimestamp(
                    ended.timestamp() - elapsed_f, tz=timezone.utc,
                ).isoformat()
    return started_at, elapsed_f


def issue_status(
    issue_number: int,
    *,
    handler: str | None = None,
    workflow: str | None = None,
    exec_jsonl_path: str | Path,
    state_dir: str | Path,
    done_dir: str | Path,
    running_uuids: set[str] | None = None,
    audit_path: str | Path | None = None,
    running_dir: str | Path | None = None,
) -> IssueStatus:
    """Return the current Issue × handler DAG status.

    ``handler`` and ``workflow`` are required to resolve the idempotency key /
    generation (same convention as ``plan_recover``).
    """
    if not handler or not workflow:
        raise ValueError("handler and workflow are required for issue_status()")

    generation, _key, records = _read_step_records(
        state_dir=state_dir,
        exec_jsonl_path=exec_jsonl_path,
        workflow_name=workflow,
        handler_name=handler,
        issue_number=issue_number,
        done_dir=done_dir,
        running_uuids=running_uuids,
    )

    done_path = Path(done_dir)
    running = running_uuids or set()
    run_dir = Path(running_dir) if running_dir is not None else None
    audit = Path(audit_path) if audit_path is not None else None

    steps: list[StepStatus] = []
    orphan_uuids: list[str] = []
    any_running = False

    for rec in records:
        status = _to_issue_status_value(rec.status)
        if status == "running":
            any_running = True

        started_at: str | None = None
        elapsed_sec: float | None = None

        if status == "running" and run_dir is not None:
            payload = _read_running_payload(run_dir, rec.uuid)
            if payload is not None:
                raw_started = payload.get("started_at")
                if isinstance(raw_started, str):
                    started_at = raw_started
                    elapsed_sec = _elapsed_since(raw_started)
        elif status != "pending" and audit is not None:
            started_at, elapsed_sec = _timing_from_audit(audit, rec.uuid)

        if (
            status == "pending"
            and rec.uuid not in running
            and read_done_content(done_path, rec.uuid) is None
            and all(dep_succeeded(done_path, dep) for dep in rec.depends)
        ):
            orphan_uuids.append(rec.uuid)

        steps.append(
            StepStatus(
                uuid=rec.uuid,
                step_name=rec.step_name,
                depends=list(rec.depends),
                status=status,
                started_at=started_at,
                elapsed_sec=elapsed_sec,
                result_path=rec.result_path,
            )
        )

    return IssueStatus(
        generation=generation,
        steps=steps,
        running=any_running,
        orphan_uuids=orphan_uuids,
    )


def running_tasks(running_dir: str | Path) -> list[RunningTask]:
    """Return RunningTask list from ``running_dir/*.json`` markers."""
    directory = Path(running_dir)
    if not directory.is_dir():
        return []

    tasks: list[RunningTask] = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.debug("skip unreadable running marker: %s", path)
            continue
        if not isinstance(data, dict):
            continue
        started_at = data.get("started_at")
        if not isinstance(started_at, str) or not started_at:
            continue
        elapsed = _elapsed_since(started_at)
        if elapsed is None:
            continue
        engine = data.get("engine")
        engine_s = engine if isinstance(engine, str) and engine else None
        pid_raw = data.get("pid")
        pid = pid_raw if isinstance(pid_raw, int) else None
        tasks.append(
            RunningTask(
                uuid=path.stem,
                started_at=started_at,
                elapsed_sec=elapsed,
                engine=engine_s,
                pid=pid,
            )
        )
    return tasks
