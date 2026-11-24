"""QuotaGate read-only paths must not create storage (Issue #7879)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ghdag.quota import QuotaGate

JST = timezone(timedelta(hours=9))


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 2, hour, minute, tzinfo=JST)


def test_snapshot_on_missing_state_creates_nothing(tmp_path: Path) -> None:
    state_path = tmp_path / "nested" / "missing" / "quota-gate.json"
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    gate = QuotaGate(state_path)

    snap = gate.snapshot()

    assert snap.engines == {}
    assert snap.draining_engines == {}
    assert snap.running_tasks == {}
    assert snap.limits == {}
    assert snap.usage == {}
    assert not state_path.parent.exists()
    assert not state_path.exists()
    assert not lock_path.exists()


def test_read_state_on_missing_state_creates_nothing(tmp_path: Path) -> None:
    state_path = tmp_path / "a" / "b" / "quota-gate.json"
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    gate = QuotaGate(state_path)

    state = gate.read_state()

    assert state["schema_version"] == 1
    assert state["engines"] == {}
    assert state["draining_engines"] == {}
    assert state["running_tasks"] == {}
    assert not state_path.parent.exists()
    assert not state_path.exists()
    assert not lock_path.exists()


def test_read_state_missing_check_is_linearized_before_concurrent_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_path = tmp_path / "quota-gate.json"
    gate = QuotaGate(state_path)
    real_exists = Path.exists
    injected = False

    def exists_after_concurrent_write(path: Path) -> bool:
        nonlocal injected
        if path == state_path and not injected:
            state_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "engines": {"claude": {"status": "paused"}},
                        "draining_engines": {},
                        "running_tasks": {},
                    }
                ),
                encoding="utf-8",
            )
            injected = True
            return False
        return real_exists(path)

    monkeypatch.setattr(Path, "exists", exists_after_concurrent_write)

    state = gate.read_state()

    assert state == {
        "schema_version": 1,
        "engines": {},
        "draining_engines": {},
        "running_tasks": {},
    }


def test_snapshot_with_existing_state_reads_under_lock(tmp_path: Path) -> None:
    state_path = tmp_path / "quota-gate.json"
    gate = QuotaGate(state_path)
    gate.report(engine="claude", status="paused", observed_at=_dt(12), resume_at=_dt(17))

    snap = gate.snapshot(now=_dt(13))

    assert snap.engines["claude"].status == "paused"


def test_modify_on_missing_state_creates_storage(tmp_path: Path) -> None:
    state_path = tmp_path / "new" / "dir" / "quota-gate.json"
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    gate = QuotaGate(state_path)

    result = gate.modify(lambda s: s.update({"resources": {"n": 1}}))

    assert result["resources"]["n"] == 1
    assert state_path.parent.exists()
    assert state_path.exists()
    assert lock_path.exists()
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["resources"]["n"] == 1
