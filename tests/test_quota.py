from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ghdag.quota import QuotaGate

JST = timezone(timedelta(hours=9))


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 2, hour, minute, tzinfo=JST)


def test_stale_available_report_is_ignored(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json")
    gate.report(engine="claude", status="paused", observed_at=_dt(12, 0), resume_at=_dt(17, 0))
    gate.admit(task_uuid="task-1", engine="claude", phase="launch", now=_dt(12, 10))
    stale = gate.report(engine="claude", status="available", observed_at=_dt(11, 59))
    assert stale.stale_ignored is True

    snapshot = gate.snapshot(now=_dt(13, 0))
    assert snapshot.engines["claude"].status == "paused"


def test_invalid_pause_ttl_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        QuotaGate(tmp_path / "quota-gate.json", pause_ttl_seconds=0)
    with pytest.raises(ValueError):
        QuotaGate(tmp_path / "quota-gate.json", pause_ttl_seconds=-1)


def test_validation_rejects_naive_timestamp_and_empty_engine(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json")
    with pytest.raises(ValueError):
        gate.report(engine="", status="paused", observed_at=_dt(12))
    with pytest.raises(ValueError):
        gate.report(
            engine="claude",
            status="paused",
            observed_at=datetime(2026, 9, 2, 12, 0),
        )


def test_begin_run_and_drain_race_never_creates_dual_registry(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json")
    for idx in range(25):
        task_uuid = f"task-{idx}"
        gate.resume(engine="claude", now=_dt(12, 0))
        done = threading.Event()

        def _begin() -> None:
            gate.begin_run(task_uuid=task_uuid, engine="claude", now=_dt(12, 1))
            done.set()

        worker = threading.Thread(target=_begin, daemon=True)
        worker.start()
        gate.drain(engine="claude", now=_dt(12, 1))
        worker.join()
        assert done.is_set()

        snapshot = gate.snapshot(now=_dt(12, 2))
        if task_uuid in snapshot.running_tasks:
            gate.finish_run(task_uuid=task_uuid)


def test_corrupted_state_is_fail_closed(tmp_path: Path) -> None:
    state_path = tmp_path / "quota-gate.json"
    state_path.write_text("{broken", encoding="utf-8")
    gate = QuotaGate(state_path, audit_path=tmp_path / "audit.jsonl")
    with pytest.raises(ValueError):
        gate.snapshot()
    with pytest.raises(ValueError):
        gate.admit(task_uuid="task-1", engine="claude", phase="launch", now=_dt(12))


_DEFAULT_STATE = {
    "schema_version": 1,
    "engines": {},
    "draining_engines": {},
    "running_tasks": {},
}


def test_modify_exception_propagates_and_state_is_unchanged(tmp_path: Path) -> None:
    state_path = tmp_path / "quota-gate.json"
    gate = QuotaGate(state_path)
    gate.modify(lambda s: s.update({"resources": {"n": 1}}))
    before = state_path.read_bytes()

    def fn(state: dict) -> dict:
        state["resources"]["n"] = 2
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        gate.modify(fn)
    assert state_path.read_bytes() == before


