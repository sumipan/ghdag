"""Quota gate for deferring DAG tasks while engine quota is paused."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal, cast

from ghdag.io.audit import append_audit_record

QuotaStatus = Literal["available", "paused"]
AdmissionPhase = Literal["enqueue", "launch", "runtime"]


@dataclass(frozen=True)
class QuotaReportResult:
    engine: str
    status: QuotaStatus
    applied: bool
    stale_ignored: bool
    observed_at: datetime
    resume_at: datetime | None
    reason: str | None


@dataclass(frozen=True)
class AdmissionDecision:
    allowed: bool
    status: str
    reason: str | None
    resume_at: datetime | None
    admitted_engine: str | None = None


@dataclass(frozen=True)
class EngineQuotaState:
    status: QuotaStatus
    observed_at: datetime
    resume_at: datetime | None
    reason: str | None
    override_until: datetime | None = None


@dataclass(frozen=True)
class DeferredTaskState:
    engine: str
    phase: AdmissionPhase
    deferred_at: datetime
    reason: str | None
    role: str | None = None
    role_engines: tuple[str, ...] = ()


@dataclass(frozen=True)
class QuotaSnapshot:
    engines: dict[str, EngineQuotaState]
    deferred_tasks: dict[str, DeferredTaskState]
    draining_engines: dict[str, "DrainState"]
    running_tasks: dict[str, "RunningTaskState"]
    limits: dict[str, int]


@dataclass(frozen=True)
class DrainState:
    started_at: datetime
    reason: str | None


@dataclass(frozen=True)
class RunningTaskState:
    engine: str
    started_at: datetime


class QuotaGate:
    """Store and evaluate engine-level quota availability."""

    def __init__(
        self,
        state_path: str | Path,
        audit_path: str | Path | None = None,
        brake_state_path: str | Path | None = None,
        pause_ttl_seconds: int | None = 3600,
        limits: Mapping[str, int] | None = None,
    ):
        if pause_ttl_seconds is not None and pause_ttl_seconds <= 0:
            raise ValueError("pause_ttl_seconds must be positive when set")
        if limits is not None:
            for engine_name, limit in limits.items():
                if not isinstance(limit, int) or limit <= 0:
                    raise ValueError("limits values must be positive integers")
                _require_non_empty(str(engine_name), "limits engine name")
        self._state_path = Path(state_path)
        self._lock_path = self._state_path.with_suffix(self._state_path.suffix + ".lock")
        self._audit_path = Path(audit_path) if audit_path else None
        self._brake_state_path = Path(brake_state_path) if brake_state_path else None
        self._pause_ttl_seconds = pause_ttl_seconds
        self._limits = dict(limits) if limits is not None else None

    def report(
        self,
        *,
        engine: str,
        status: QuotaStatus,
        observed_at: datetime,
        resume_at: datetime | None = None,
        reason: str | None = None,
    ) -> QuotaReportResult:
        engine_name = _require_non_empty(engine, "engine")
        observed = _require_aware(observed_at, "observed_at")
        if status not in {"available", "paused"}:
            raise ValueError("status must be 'available' or 'paused'")
        if resume_at is not None:
            resume_at = _require_aware(resume_at, "resume_at")
        elif status == "paused" and self._pause_ttl_seconds is not None:
            resume_at = observed + timedelta(seconds=self._pause_ttl_seconds)

        with self._lock(exclusive=True):
            state = self._load_state_unlocked()
            existing = state["engines"].get(engine_name)
            if existing is not None:
                prev_observed = _parse_dt(existing["observed_at"], "observed_at")
                if observed < prev_observed:
                    return QuotaReportResult(
                        engine=engine_name,
                        status=status,
                        applied=False,
                        stale_ignored=True,
                        observed_at=observed,
                        resume_at=resume_at,
                        reason=reason,
                    )
                if status == "paused":
                    override_until = _parse_optional_dt(
                        existing.get("override_until"), "override_until"
                    )
                    if override_until is not None and observed < override_until:
                        return QuotaReportResult(
                            engine=engine_name,
                            status=status,
                            applied=False,
                            stale_ignored=False,
                            observed_at=observed,
                            resume_at=resume_at,
                            reason=reason,
                        )

            applied_state = {
                "status": status,
                "observed_at": _iso(observed),
                "resume_at": _iso(resume_at),
                "reason": reason,
            }
            if status == "available":
                applied_state["override_until"] = None
            elif existing is not None and existing.get("override_until"):
                applied_state["override_until"] = existing.get("override_until")
            state["engines"][engine_name] = applied_state
            resumed = self._release_for_engine(state, engine_name, observed)
            self._write_state_unlocked(state)

        self._audit_state_changed(
            engine=engine_name,
            status=status,
            observed_at=observed,
            resume_at=resume_at,
            reason=reason,
            stale_ignored=False,
        )
        for task_uuid in resumed:
            self._audit_task_resumed(task_uuid=task_uuid, engine=engine_name, observed_at=observed)
        return QuotaReportResult(
            engine=engine_name,
            status=status,
            applied=True,
            stale_ignored=False,
            observed_at=observed,
            resume_at=resume_at,
            reason=reason,
        )

    def clear(
        self,
        *,
        engine: str,
        observed_at: datetime,
        ttl_seconds: int | None = None,
    ) -> QuotaReportResult:
        engine_name = _require_non_empty(engine, "engine")
        observed = _require_aware(observed_at, "observed_at")

        with self._lock(exclusive=True):
            state = self._load_state_unlocked()
            existing = state["engines"].get(engine_name)
            if existing is not None:
                prev_observed = _parse_dt(existing["observed_at"], "observed_at")
                if observed < prev_observed:
                    return QuotaReportResult(
                        engine=engine_name,
                        status="available",
                        applied=False,
                        stale_ignored=True,
                        observed_at=observed,
                        resume_at=None,
                        reason=None,
                    )
            if ttl_seconds is None:
                state["engines"].pop(engine_name, None)
            else:
                if ttl_seconds < 0:
                    raise ValueError("ttl_seconds must be >= 0")
                override_until = observed + timedelta(seconds=ttl_seconds)
                state["engines"][engine_name] = {
                    "status": "available",
                    "observed_at": _iso(observed),
                    "resume_at": None,
                    "reason": None,
                    "override_until": _iso(override_until),
                }
            resumed = self._release_for_engine(state, engine_name, observed)
            self._write_state_unlocked(state)

        self._audit_state_changed(
            engine=engine_name,
            status="available",
            observed_at=observed,
            resume_at=None,
            reason=None,
            stale_ignored=False,
        )
        for task_uuid in resumed:
            self._audit_task_resumed(task_uuid=task_uuid, engine=engine_name, observed_at=observed)
        return QuotaReportResult(
            engine=engine_name,
            status="available",
            applied=True,
            stale_ignored=False,
            observed_at=observed,
            resume_at=None,
            reason=None,
        )

    def drain(
        self,
        *,
        engine: str,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> None:
        engine_name = _require_non_empty(engine, "engine")
        current = _aware_now(now)
        changed = False

        with self._lock(exclusive=True):
            state = self._load_state_unlocked()
            if engine_name not in state["draining_engines"]:
                state["draining_engines"][engine_name] = {
                    "started_at": _iso(current),
                    "reason": reason,
                }
                self._write_state_unlocked(state)
                changed = True

        if changed:
            self._audit_engine_drain_started(engine=engine_name, observed_at=current, reason=reason)

    def resume(
        self,
        *,
        engine: str,
        now: datetime | None = None,
    ) -> list[str]:
        engine_name = _require_non_empty(engine, "engine")
        current = _aware_now(now)
        changed = False
        drain_removed = False
        released: list[str] = []

        with self._lock(exclusive=True):
            state = self._load_state_unlocked()
            drain_removed = state["draining_engines"].pop(engine_name, None) is not None
            released = self._release_for_engine(state, engine_name, current)
            if drain_removed or released:
                self._write_state_unlocked(state)
                changed = True

        if drain_removed:
            self._audit_engine_drain_resumed(engine=engine_name, observed_at=current)
        if changed:
            for task_uuid in released:
                self._audit_task_resumed(task_uuid=task_uuid, engine=engine_name, observed_at=current)
        return released

    def admit(
        self,
        *,
        task_uuid: str,
        engine: str | None,
        phase: AdmissionPhase,
        role: str | None = None,
        role_engines: list[str] | None = None,
        now: datetime | None = None,
        reason: str | None = None,
    ) -> AdmissionDecision:
        _require_non_empty(task_uuid, "task_uuid")
        if phase not in {"enqueue", "launch", "runtime"}:
            raise ValueError("phase must be enqueue/launch/runtime")
        if engine is None and role is None:
            return AdmissionDecision(True, "ALLOWED", None, None)

        engine_name = _require_non_empty(engine, "engine") if engine is not None else ""
        current = _aware_now(now)

        with self._lock(exclusive=True):
            state = self._load_state_unlocked()
            decision = self._evaluate_admission_unlocked(
                state=state,
                engine=engine_name,
                now=current,
                fallback_reason=reason,
                role=role,
                role_engines=role_engines,
                task_uuid=task_uuid,
            )
            if decision.allowed:
                return AdmissionDecision(True, "ALLOWED", None, None)

            deferred_payload = _deferred_payload(
                engine=engine_name,
                phase=phase,
                current=current,
                reason=decision.reason,
                role=role,
                role_engines=role_engines,
            )
            previous_deferred = state["deferred_tasks"].get(task_uuid)
            removed_running = state["running_tasks"].pop(task_uuid, None) is not None
            state["deferred_tasks"][task_uuid] = deferred_payload
            changed = previous_deferred != deferred_payload or removed_running
            if changed:
                self._write_state_unlocked(state)

        if changed:
            self._audit_task_deferred(
                task_uuid=task_uuid,
                engine=engine_name or (role_engines[0] if role_engines else "unknown"),
                phase=phase,
                observed_at=current,
                resume_at=decision.resume_at,
                reason=decision.reason,
            )
        return decision

    def defer(
        self,
        task_uuid: str,
        *,
        engine: str,
        after: datetime | None = None,
        role_engines: list[str] | None = None,
        reason: str | None = None,
    ) -> None:
        """Explicitly register a task as deferred (called by external dispatch)."""
        _require_non_empty(task_uuid, "task_uuid")
        engine_name = _require_non_empty(engine, "engine")
        current = _aware_now(after)

        deferred_payload: dict = {
            "engine": engine_name,
            "phase": "enqueue",
            "deferred_at": _iso(current),
            "reason": reason,
        }
        if role_engines:
            deferred_payload["role_engines"] = list(role_engines)

        with self._lock(exclusive=True):
            state = self._load_state_unlocked()
            state["deferred_tasks"][task_uuid] = deferred_payload
            self._write_state_unlocked(state)

        self._audit_task_deferred(
            task_uuid=task_uuid,
            engine=engine_name,
            phase="enqueue",
            observed_at=current,
            resume_at=None,
            reason=reason,
        )

    def begin_run(
        self,
        *,
        task_uuid: str,
        engine: str | None,
        role: str | None = None,
        role_engines: list[str] | None = None,
        now: datetime | None = None,
    ) -> AdmissionDecision:
        _require_non_empty(task_uuid, "task_uuid")
        if engine is None and role is None:
            return AdmissionDecision(True, "ALLOWED", None, None)

        engine_name = _require_non_empty(engine, "engine") if engine is not None else ""
        current = _aware_now(now)
        changed = False

        with self._lock(exclusive=True):
            state = self._load_state_unlocked()
            decision = self._evaluate_admission_unlocked(
                state=state,
                engine=engine_name,
                now=current,
                fallback_reason=None,
                role=role,
                role_engines=role_engines,
                task_uuid=task_uuid,
                check_engine_limits=True,
            )
            if decision.allowed:
                removed_deferred = state["deferred_tasks"].pop(task_uuid, None) is not None
                previous_running = state["running_tasks"].get(task_uuid)
                if decision.admitted_engine is not None:
                    record_engine = decision.admitted_engine
                elif engine is not None:
                    record_engine = engine_name
                else:
                    record_engine = "unknown"
                running_payload: dict[str, Any] = {
                    "engine": record_engine,
                    "started_at": _iso(current),
                }
                if role is not None:
                    running_payload["role"] = role
                limits_changed = False
                if self._limits is not None and state.get("limits") != self._limits:
                    state["limits"] = dict(self._limits)
                    limits_changed = True
                changed = previous_running != running_payload or removed_deferred
                state["running_tasks"][task_uuid] = running_payload
                if changed or limits_changed:
                    self._write_state_unlocked(state)
            elif decision.reason == "engine_limit":
                pass
            else:
                deferred_payload = _deferred_payload(
                    engine=engine_name,
                    phase="launch",
                    current=current,
                    reason=decision.reason,
                    role=role,
                    role_engines=role_engines,
                )
                previous_deferred = state["deferred_tasks"].get(task_uuid)
                removed_running = state["running_tasks"].pop(task_uuid, None) is not None
                state["deferred_tasks"][task_uuid] = deferred_payload
                changed = previous_deferred != deferred_payload or removed_running
                if changed:
                    self._write_state_unlocked(state)

        if changed:
            audit_engine = (
                decision.admitted_engine
                if decision.admitted_engine is not None
                else (engine_name or (role_engines[0] if role_engines else "unknown"))
            )
            if decision.allowed:
                self._audit_task_running_started(
                    task_uuid=task_uuid,
                    engine=audit_engine,
                    observed_at=current,
                )
            else:
                self._audit_task_deferred(
                    task_uuid=task_uuid,
                    engine=audit_engine,
                    phase="launch",
                    observed_at=current,
                    resume_at=decision.resume_at,
                    reason=decision.reason,
                )
        return decision

    def finish_run(self, *, task_uuid: str) -> bool:
        task_id = _require_non_empty(task_uuid, "task_uuid")
        with self._lock(exclusive=True):
            state = self._load_state_unlocked()
            running = state["running_tasks"].pop(task_id, None)
            if running is None:
                return False
            self._write_state_unlocked(state)

        engine = str(running.get("engine", "unknown"))
        self._audit_task_running_finished(
            task_uuid=task_id,
            engine=engine,
            observed_at=datetime.now(timezone.utc),
        )
        return True

    def wait_idle(
        self,
        engine: str,
        timeout: float | None = None,
        poll_interval: float = 0.1,
    ) -> bool:
        engine_name = _require_non_empty(engine, "engine")
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be >= 0")
        if poll_interval <= 0:
            raise ValueError("poll_interval must be > 0")

        deadline = None if timeout is None else (time.monotonic() + timeout)
        while True:
            snapshot = self.snapshot()
            running_count = sum(
                1 for state in snapshot.running_tasks.values() if state.engine == engine_name
            )
            if running_count == 0:
                return True
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(poll_interval)

    def release_ready(self, *, now: datetime | None = None) -> list[str]:
        current = _aware_now(now)
        with self._lock(exclusive=True):
            state = self._load_state_unlocked()
            released: list[tuple[str, str]] = []
            for engine_name, payload in list(state["engines"].items()):
                engine_state = _to_engine_state(payload)
                if engine_state is None:
                    continue
                if engine_state.status == "available":
                    for task_uuid in self._release_for_engine(state, engine_name, current):
                        released.append((task_uuid, engine_name))
                    continue
                if engine_state.resume_at is not None and engine_state.resume_at <= current:
                    state["engines"][engine_name] = {
                        "status": "available",
                        "observed_at": _iso(current),
                        "resume_at": None,
                        "reason": engine_state.reason,
                    }
                    for task_uuid in self._release_for_engine(state, engine_name, current):
                        released.append((task_uuid, engine_name))

            engines_checked = set(state["engines"].keys())
            brake_state = self._load_brake_state()
            for task_uuid, deferred in list(state["deferred_tasks"].items()):
                role_engines = deferred.get("role_engines")
                engines_to_check: list[str] = (
                    list(role_engines) if isinstance(role_engines, list) and role_engines
                    else [str(deferred.get("engine", ""))]
                )
                for eng in engines_to_check:
                    if eng in engines_checked:
                        continue
                    if not _engine_effective_available(state, eng, current, brake_state):
                        continue
                    released.append((task_uuid, eng))
                    del state["deferred_tasks"][task_uuid]
                    break

            if released:
                self._write_state_unlocked(state)

        for task_uuid, engine_name in released:
            self._audit_task_resumed(task_uuid=task_uuid, engine=engine_name, observed_at=current)
        return [uuid for uuid, _ in released]

    def read_state(self) -> dict:
        """Return the raw state dict under a shared lock.

        A missing state file yields the default state (no file is created).
        Invalid JSON or an unknown schema raises ``ValueError``.
        """
        with self._lock(exclusive=False):
            return self._load_state_unlocked()

    def modify(self, fn: Callable[[dict], dict | None]) -> dict:
        """Read-modify-write the raw state dict under an exclusive lock.

        ``fn`` receives the current state and may mutate it in place (return
        ``None``) or return a new dict. The resulting dict is written and
        returned. If ``fn`` raises, nothing is written and the exception
        propagates.
        """
        with self._lock(exclusive=True):
            state = self._load_state_unlocked()
            new = fn(state)
            result = state if new is None else new
            self._write_state_unlocked(result)
            return result

    def snapshot(self, *, now: datetime | None = None) -> QuotaSnapshot:
        current = _aware_now(now)
        with self._lock(exclusive=False):
            state = self._load_state_unlocked()
        engines: dict[str, EngineQuotaState] = {}
        for name, payload in state["engines"].items():
            eng = _to_engine_state(payload)
            if eng is None:
                continue
            if eng.status == "paused" and eng.resume_at is not None and eng.resume_at <= current:
                engines[name] = EngineQuotaState(
                    status="available",
                    observed_at=eng.observed_at,
                    resume_at=None,
                    reason=eng.reason,
                )
            else:
                engines[name] = eng
        deferred_tasks = {
            task_uuid: DeferredTaskState(
                engine=str(payload["engine"]),
                phase=str(payload["phase"]),  # type: ignore[arg-type]
                deferred_at=_parse_dt(str(payload["deferred_at"]), "deferred_at"),
                reason=payload.get("reason"),
                role=payload.get("role"),
                role_engines=tuple(payload.get("role_engines") or ()),
            )
            for task_uuid, payload in state["deferred_tasks"].items()
        }
        draining_engines: dict[str, DrainState] = {}
        for engine_name, payload in state["draining_engines"].items():
            drain_state = _to_drain_state(payload)
            if drain_state is not None:
                draining_engines[engine_name] = drain_state
        running_tasks = {
            task_uuid: _to_running_state(payload)
            for task_uuid, payload in state["running_tasks"].items()
        }
        limits: dict[str, int] = {}
        limits_raw = state.get("limits")
        if isinstance(limits_raw, dict):
            for name, value in limits_raw.items():
                if isinstance(value, int):
                    limits[str(name)] = value
        return QuotaSnapshot(
            engines=engines,
            deferred_tasks=deferred_tasks,
            draining_engines=draining_engines,
            running_tasks=running_tasks,
            limits=limits,
        )

    def _release_for_engine(self, state: dict, engine: str, observed_at: datetime) -> list[str]:
        if engine in state["draining_engines"]:
            return []
        brake_state = self._load_brake_state()
        released: list[str] = []
        for task_uuid, deferred in list(state["deferred_tasks"].items()):
            if not self._should_release_deferred(state, deferred, engine, observed_at, brake_state):
                continue
            released.append(task_uuid)
            del state["deferred_tasks"][task_uuid]
        return released

    def _should_release_deferred(
        self,
        state: dict,
        deferred: dict,
        engine: str,
        observed_at: datetime,
        brake_state: dict | None = None,
    ) -> bool:
        role_engines = deferred.get("role_engines")
        if isinstance(role_engines, list) and role_engines:
            if engine not in role_engines:
                return False
            return any(
                _engine_effective_available(state, role_engine, observed_at, brake_state)
                for role_engine in role_engines
            )
        if deferred.get("engine") != engine:
            return False
        return _engine_effective_available(state, engine, observed_at, brake_state)

    def _load_brake_state(self) -> dict | None:
        if self._brake_state_path is None or not self._brake_state_path.exists():
            return None
        try:
            loaded = json.loads(self._brake_state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return loaded if isinstance(loaded, dict) else None

    def _load_state_unlocked(self) -> dict:
        """Private: use read_state() / modify()."""
        if not self._state_path.exists():
            return {
                "schema_version": 1,
                "engines": {},
                "deferred_tasks": {},
                "draining_engines": {},
                "running_tasks": {},
            }
        try:
            payload = cast(dict[str, Any], json.loads(self._state_path.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            self._audit_state_error(f"invalid_json: {exc}")
            raise ValueError("quota state file is corrupted") from exc
        if payload.get("schema_version") != 1:
            self._audit_state_error("unknown_schema_version")
            raise ValueError("unsupported quota state schema version")
        payload.setdefault("engines", {})
        payload.setdefault("deferred_tasks", {})
        payload.setdefault("draining_engines", {})
        payload.setdefault("running_tasks", {})
        return payload

    def _evaluate_admission_unlocked(
        self,
        *,
        state: dict,
        engine: str,
        now: datetime,
        fallback_reason: str | None,
        role: str | None = None,
        role_engines: list[str] | None = None,
        task_uuid: str = "",
        check_engine_limits: bool = False,
    ) -> AdmissionDecision:
        if role is not None and role_engines:
            return self._evaluate_role_admission_unlocked(
                state=state,
                role=role,
                role_engines=role_engines,
                now=now,
                fallback_reason=fallback_reason,
                task_uuid=task_uuid,
                check_engine_limits=check_engine_limits,
            )

        engine_state = _to_engine_state(state["engines"].get(engine))
        if engine_state is not None and _is_paused(engine_state, now):
            deferred_reason = fallback_reason or engine_state.reason or "quota paused"
            return AdmissionDecision(
                allowed=False,
                status="DEFERRED",
                reason=deferred_reason,
                resume_at=engine_state.resume_at,
            )
        drain_state = _to_drain_state(state["draining_engines"].get(engine))
        if drain_state is not None:
            deferred_reason = fallback_reason or drain_state.reason or "engine draining"
            return AdmissionDecision(
                allowed=False,
                status="DEFERRED",
                reason=deferred_reason,
                resume_at=None,
            )
        if check_engine_limits and self._limits is not None and engine in self._limits:
            if self._count_running_for_engine(state, engine, task_uuid) >= self._limits[engine]:
                return AdmissionDecision(
                    allowed=False,
                    status="DEFERRED",
                    reason="engine_limit",
                    resume_at=None,
                )
        return AdmissionDecision(True, "ALLOWED", None, None)

    def _evaluate_role_admission_unlocked(
        self,
        *,
        state: dict,
        role: str,
        role_engines: list[str],
        now: datetime,
        fallback_reason: str | None,
        task_uuid: str,
        check_engine_limits: bool,
    ) -> AdmissionDecision:
        resume_candidates: list[datetime] = []
        saw_unavailable = False
        saw_limit_block = False
        for role_engine in role_engines:
            if not _engine_effective_available(state, role_engine, now):
                saw_unavailable = True
                engine_state = _to_engine_state(state["engines"].get(role_engine))
                if engine_state is not None and engine_state.resume_at is not None:
                    resume_candidates.append(engine_state.resume_at)
                continue
            if check_engine_limits and self._limits is not None and role_engine in self._limits:
                if self._count_running_for_engine(state, role_engine, task_uuid) >= self._limits[
                    role_engine
                ]:
                    saw_limit_block = True
                    continue
            return AdmissionDecision(
                True,
                "ALLOWED",
                None,
                None,
                admitted_engine=role_engine,
            )

        if saw_limit_block and not saw_unavailable:
            return AdmissionDecision(
                allowed=False,
                status="DEFERRED",
                reason="engine_limit",
                resume_at=None,
            )
        deferred_reason = fallback_reason or f"all engines paused for role {role}"
        earliest_resume = min(resume_candidates) if resume_candidates else None
        return AdmissionDecision(
            allowed=False,
            status="DEFERRED",
            reason=deferred_reason,
            resume_at=earliest_resume,
        )

    def _count_running_for_engine(self, state: dict, engine: str, exclude_uuid: str) -> int:
        count = 0
        for task_id, payload in state["running_tasks"].items():
            if task_id == exclude_uuid:
                continue
            running_engine = payload.get("engine")
            if running_engine != engine:
                continue
            if running_engine in ("", None):
                continue
            count += 1
        return count

    def _write_state_unlocked(self, state: dict) -> None:
        """Private: use read_state() / modify()."""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{self._state_path.name}.",
            suffix=".tmp",
            dir=str(self._state_path.parent),
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, self._state_path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def _lock(self, *, exclusive: bool):
        """Private: use read_state() / modify()."""
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self._lock_path, "a+", encoding="utf-8")
        op = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(fh, op)

        class _LockCtx:
            def __enter__(self_nonlocal):
                return fh

            def __exit__(self_nonlocal, exc_type, exc, tb):
                fcntl.flock(fh, fcntl.LOCK_UN)
                fh.close()
                return False

        return _LockCtx()

    def _audit_state_changed(
        self,
        *,
        engine: str,
        status: QuotaStatus,
        observed_at: datetime,
        resume_at: datetime | None,
        reason: str | None,
        stale_ignored: bool,
    ) -> None:
        self._append_audit(
            {
                "schema_version": 1,
                "event_type": "quota_state_changed",
                "engine": engine,
                "status": status,
                "observed_at": _iso(observed_at),
                "resume_at": _iso(resume_at),
                "reason": reason,
                "stale_ignored": stale_ignored,
                "correlation_id": f"quota:{engine}",
            }
        )

    def _audit_task_deferred(
        self,
        *,
        task_uuid: str,
        engine: str,
        phase: AdmissionPhase,
        observed_at: datetime,
        resume_at: datetime | None,
        reason: str | None,
    ) -> None:
        self._append_audit(
            {
                "schema_version": 1,
                "event_type": "task_deferred",
                "uuid": task_uuid,
                "engine": engine,
                "phase": phase,
                "status": "deferred",
                "observed_at": _iso(observed_at),
                "resume_at": _iso(resume_at),
                "reason": reason,
                "correlation_id": task_uuid,
            }
        )

    def _audit_task_resumed(
        self,
        *,
        task_uuid: str,
        engine: str,
        observed_at: datetime,
    ) -> None:
        self._append_audit(
            {
                "schema_version": 1,
                "event_type": "task_resumed",
                "uuid": task_uuid,
                "engine": engine,
                "status": "resumed",
                "observed_at": _iso(observed_at),
                "correlation_id": task_uuid,
            }
        )

    def _audit_engine_drain_started(
        self,
        *,
        engine: str,
        observed_at: datetime,
        reason: str | None,
    ) -> None:
        self._append_audit(
            {
                "schema_version": 1,
                "event_type": "engine_drain_started",
                "engine": engine,
                "reason": reason,
                "observed_at": _iso(observed_at),
                "correlation_id": f"quota:{engine}",
            }
        )

    def _audit_engine_drain_resumed(
        self,
        *,
        engine: str,
        observed_at: datetime,
    ) -> None:
        self._append_audit(
            {
                "schema_version": 1,
                "event_type": "engine_drain_resumed",
                "engine": engine,
                "observed_at": _iso(observed_at),
                "correlation_id": f"quota:{engine}",
            }
        )

    def _audit_task_running_started(
        self,
        *,
        task_uuid: str,
        engine: str,
        observed_at: datetime,
    ) -> None:
        self._append_audit(
            {
                "schema_version": 1,
                "event_type": "task_running_started",
                "uuid": task_uuid,
                "engine": engine,
                "observed_at": _iso(observed_at),
                "correlation_id": task_uuid,
            }
        )

    def _audit_task_running_finished(
        self,
        *,
        task_uuid: str,
        engine: str,
        observed_at: datetime,
    ) -> None:
        self._append_audit(
            {
                "schema_version": 1,
                "event_type": "task_running_finished",
                "uuid": task_uuid,
                "engine": engine,
                "observed_at": _iso(observed_at),
                "correlation_id": task_uuid,
            }
        )

    def _audit_state_error(self, reason: str) -> None:
        self._append_audit(
            {
                "schema_version": 1,
                "event_type": "quota_state_error",
                "reason": reason,
                "correlation_id": "quota:state",
            }
        )

    def _append_audit(self, record: dict) -> None:
        if self._audit_path is None:
            return
        try:
            append_audit_record(self._audit_path, {
                **record,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except OSError:
            return


def _require_non_empty(value: str, field_name: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} must not be empty")
    return stripped


def _require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _aware_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    return _require_aware(now, "now")


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def _parse_dt(raw: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"invalid datetime in {field_name}") from exc
    return _require_aware(parsed, field_name)


def _to_engine_state(payload: dict | None) -> EngineQuotaState | None:
    if payload is None:
        return None
    status = payload.get("status")
    if status not in {"available", "paused"}:
        raise ValueError("invalid engine status in quota state")
    observed = _parse_dt(str(payload.get("observed_at")), "observed_at")
    resume_raw = payload.get("resume_at")
    resume_at = _parse_dt(resume_raw, "resume_at") if isinstance(resume_raw, str) and resume_raw else None
    override_until = _parse_optional_dt(payload.get("override_until"), "override_until")
    reason = payload.get("reason")
    if reason is not None and not isinstance(reason, str):
        reason = str(reason)
    return EngineQuotaState(
        status=status,
        observed_at=observed,
        resume_at=resume_at,
        reason=reason,
        override_until=override_until,
    )


def _to_drain_state(payload: dict | None) -> DrainState | None:
    if payload is None:
        return None
    started_at = _parse_dt(str(payload.get("started_at")), "started_at")
    reason = payload.get("reason")
    if reason is not None and not isinstance(reason, str):
        reason = str(reason)
    return DrainState(started_at=started_at, reason=reason)


def _to_running_state(payload: dict | None) -> RunningTaskState:
    if payload is None:
        raise ValueError("running task payload must not be null")
    engine_raw = payload.get("engine")
    engine = str(engine_raw) if engine_raw is not None else ""
    started_at = _parse_dt(str(payload.get("started_at")), "started_at")
    return RunningTaskState(engine=engine, started_at=started_at)


def _is_paused(state: EngineQuotaState, now: datetime) -> bool:
    if state.status != "paused":
        return False
    if state.resume_at is None:
        return True
    return now < state.resume_at


def _parse_optional_dt(raw: object, field_name: str) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    return _parse_dt(raw, field_name)


def _engine_effective_available(
    state: dict,
    engine: str,
    observed_at: datetime,
    brake_state: dict | None = None,
) -> bool:
    if engine in state["draining_engines"]:
        return False
    if brake_state is not None:
        brake_engines = brake_state.get("engines", {})
        brake_eng = brake_engines.get(engine, {})
        if isinstance(brake_eng, dict) and brake_eng.get("status") == "paused":
            return False
    eng_payload = state["engines"].get(engine)
    engine_state = _to_engine_state(eng_payload)
    if eng_payload is None:
        return True
    if engine_state is None:
        return False
    if engine_state.status == "available":
        return True
    if engine_state.resume_at is not None and engine_state.resume_at <= observed_at:
        return True
    return False


def _deferred_payload(
    *,
    engine: str,
    phase: AdmissionPhase,
    current: datetime,
    reason: str | None,
    role: str | None,
    role_engines: list[str] | None,
) -> dict:
    payload: dict = {
        "engine": engine or (role_engines[0] if role_engines else "unknown"),
        "phase": phase,
        "deferred_at": _iso(current),
        "reason": reason,
    }
    if role is not None:
        payload["role"] = role
    if role_engines:
        payload["role_engines"] = list(role_engines)
    return payload
