"""Tests for QuotaGate.defer() and brake_state_path integration."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ghdag.quota import QuotaGate

UTC = timezone.utc


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 22, hour, minute, tzinfo=UTC)


def _write_brake(path: Path, engines: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "engines": engines}), encoding="utf-8")


# --- AC-1: defer() records entry in deferred_tasks ---

def test_defer_records_engine_in_deferred_tasks(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json")
    gate.defer("task-1", engine="claude", after=_dt(12), reason="retry signal")
    snap = gate.snapshot()
    assert "task-1" in snap.deferred_tasks
    assert snap.deferred_tasks["task-1"].engine == "claude"
    assert snap.deferred_tasks["task-1"].reason == "retry signal"


def test_defer_overwrites_on_duplicate_call(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json")
    gate.defer("task-1", engine="claude", after=_dt(12), reason="first")
    gate.defer("task-1", engine="claude", after=_dt(13), reason="second")
    snap = gate.snapshot()
    assert snap.deferred_tasks["task-1"].reason == "second"


def test_defer_without_after_uses_current_time(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json")
    gate.defer("task-1", engine="claude")
    snap = gate.snapshot()
    assert "task-1" in snap.deferred_tasks


def test_defer_with_role_engines(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json")
    gate.defer("task-1", engine="claude", role_engines=["claude", "cursor"])
    snap = gate.snapshot()
    assert snap.deferred_tasks["task-1"].role_engines == ("claude", "cursor")


# --- AC-2: defer() → release_ready() → task released when engine available ---

def test_defer_then_release_ready_returns_uuid_when_available(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json")
    gate.report(engine="claude", status="paused", observed_at=_dt(12), resume_at=_dt(17))
    gate.defer("task-1", engine="claude", after=_dt(12, 30))

    released = gate.release_ready(now=_dt(17))
    assert "task-1" in released
    assert "task-1" not in gate.snapshot().deferred_tasks


def test_defer_not_released_while_engine_still_paused(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json")
    gate.report(engine="claude", status="paused", observed_at=_dt(12), resume_at=_dt(17))
    gate.defer("task-1", engine="claude", after=_dt(12, 30))

    released = gate.release_ready(now=_dt(16))
    assert "task-1" not in released
    assert "task-1" in gate.snapshot().deferred_tasks


def test_defer_released_when_engine_available_no_quota_record(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json")
    gate.defer("task-1", engine="claude", after=_dt(12))

    released = gate.release_ready(now=_dt(12, 1))
    assert "task-1" in released


# --- AC-3: brake_state_path prevents release while brake paused ---

def test_brake_paused_engine_not_released(tmp_path: Path) -> None:
    brake_path = tmp_path / "issuesmith-brake.json"
    _write_brake(brake_path, {"claude": {"status": "paused", "reason": "budget hit"}})

    gate = QuotaGate(tmp_path / "quota-gate.json", brake_state_path=brake_path)
    gate.report(engine="claude", status="available", observed_at=_dt(12))
    gate.defer("task-1", engine="claude", after=_dt(12))

    released = gate.release_ready(now=_dt(12, 1))
    assert "task-1" not in released
    assert "task-1" in gate.snapshot().deferred_tasks


def test_brake_available_engine_is_released(tmp_path: Path) -> None:
    brake_path = tmp_path / "issuesmith-brake.json"
    _write_brake(brake_path, {"claude": {"status": "available"}})

    gate = QuotaGate(tmp_path / "quota-gate.json", brake_state_path=brake_path)
    gate.report(engine="claude", status="available", observed_at=_dt(12))
    gate.defer("task-1", engine="claude", after=_dt(12))

    released = gate.release_ready(now=_dt(12, 1))
    assert "task-1" in released


def test_brake_file_not_exist_skips_brake_check(tmp_path: Path) -> None:
    brake_path = tmp_path / "issuesmith-brake.json"

    gate = QuotaGate(tmp_path / "quota-gate.json", brake_state_path=brake_path)
    gate.report(engine="claude", status="available", observed_at=_dt(12))
    gate.defer("task-1", engine="claude", after=_dt(12))

    released = gate.release_ready(now=_dt(12, 1))
    assert "task-1" in released


def test_brake_paused_one_engine_does_not_block_other(tmp_path: Path) -> None:
    brake_path = tmp_path / "issuesmith-brake.json"
    _write_brake(brake_path, {"claude": {"status": "paused"}})

    gate = QuotaGate(tmp_path / "quota-gate.json", brake_state_path=brake_path)
    gate.report(engine="claude", status="available", observed_at=_dt(12))
    gate.report(engine="codex", status="available", observed_at=_dt(12))
    gate.defer("claude-task", engine="claude", after=_dt(12))
    gate.defer("codex-task", engine="codex", after=_dt(12))

    released = gate.release_ready(now=_dt(12, 1))
    assert "claude-task" not in released
    assert "codex-task" in released


def test_brake_empty_file_treated_as_no_brake(tmp_path: Path) -> None:
    brake_path = tmp_path / "issuesmith-brake.json"
    _write_brake(brake_path, {})

    gate = QuotaGate(tmp_path / "quota-gate.json", brake_state_path=brake_path)
    gate.report(engine="claude", status="available", observed_at=_dt(12))
    gate.defer("task-1", engine="claude", after=_dt(12))

    released = gate.release_ready(now=_dt(12, 1))
    assert "task-1" in released
