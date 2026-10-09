"""Engine concurrency limits and running_tasks engine recording."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ghdag.quota import QuotaGate

UTC = timezone.utc


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 22, hour, minute, tzinfo=UTC)


def test_engine_limit_denies_without_deferred_tasks_or_audit(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    gate = QuotaGate(tmp_path / "quota-gate.json", audit_path=audit_path, limits={"claude": 2})
    gate.begin_run(task_uuid="t1", engine="claude", now=_dt(12))
    gate.begin_run(task_uuid="t2", engine="claude", now=_dt(12))
    decision = gate.begin_run(task_uuid="t3", engine="claude", now=_dt(12, 1))
    assert decision.allowed is False
    assert decision.status == "DEFERRED"
    assert decision.reason == "engine_limit"
    snap = gate.snapshot()
    assert "t3" not in snap.deferred_tasks
    assert audit_path.exists() is False or "task_deferred" not in audit_path.read_text(encoding="utf-8")


def test_engine_limit_allows_below_limit(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json", limits={"claude": 2})
    gate.begin_run(task_uuid="t1", engine="claude", now=_dt(12))
    decision = gate.begin_run(task_uuid="t2", engine="claude", now=_dt(12))
    assert decision.allowed is True


def test_unlisted_engine_is_unlimited(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json", limits={"claude": 1})
    gate.begin_run(task_uuid="t1", engine="codex", now=_dt(12))
    gate.begin_run(task_uuid="t2", engine="codex", now=_dt(12))
    decision = gate.begin_run(task_uuid="t3", engine="codex", now=_dt(12))
    assert decision.allowed is True


def test_invalid_limit_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        QuotaGate(tmp_path / "quota-gate.json", limits={"claude": 0})


def test_begin_run_does_not_count_self_for_limit(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json", limits={"claude": 1})
    gate.begin_run(task_uuid="t1", engine="claude", now=_dt(12))
    decision = gate.begin_run(task_uuid="t1", engine="claude", now=_dt(12, 1))
    assert decision.allowed is True


def test_role_task_picks_next_engine_under_limit(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json", limits={"claude": 1})
    gate.begin_run(task_uuid="running-claude", engine="claude", now=_dt(12))
    decision = gate.begin_run(
        task_uuid="role-task",
        engine=None,
        role="impl",
        role_engines=["claude", "codex"],
        now=_dt(12, 1),
    )
    assert decision.allowed is True
    state = gate.read_state()["running_tasks"]["role-task"]
    assert state["engine"] == "codex"
    assert state["role"] == "impl"


def test_role_task_denied_when_all_role_engines_at_limit(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json", limits={"claude": 1, "codex": 1})
    gate.begin_run(task_uuid="c1", engine="claude", now=_dt(12))
    gate.begin_run(task_uuid="x1", engine="codex", now=_dt(12))
    decision = gate.begin_run(
        task_uuid="role-task",
        engine=None,
        role="impl",
        role_engines=["claude", "codex"],
        now=_dt(12, 1),
    )
    assert decision.allowed is False
    assert decision.reason == "engine_limit"


def test_legacy_state_with_empty_engine_and_limits_persist(tmp_path: Path) -> None:
    state_path = tmp_path / "quota-gate.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "engines": {},
                "deferred_tasks": {},
                "draining_engines": {},
                "running_tasks": {"legacy": {"engine": "", "started_at": _dt(11).isoformat()}},
            }
        ),
        encoding="utf-8",
    )
    gate = QuotaGate(state_path, limits={"claude": 2})
    assert gate.snapshot().limits == {}
    gate.begin_run(task_uuid="t1", engine="claude", now=_dt(12))
    assert gate.read_state()["limits"] == {"claude": 2}
    snap = gate.snapshot()
    assert snap.running_tasks["legacy"].engine == ""
