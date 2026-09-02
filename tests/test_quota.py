from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ghdag.quota import QuotaGate

JST = timezone(timedelta(hours=9))


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 2, hour, minute, tzinfo=JST)


def test_report_and_admit_returns_deferred_with_resume_at(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json")
    gate.report(
        engine="claude",
        status="paused",
        observed_at=_dt(12, 0),
        resume_at=_dt(17, 0),
        reason="five-hour quota exhausted",
    )

    decision = gate.admit(
        task_uuid="task-1",
        engine="claude",
        phase="launch",
        now=_dt(12, 30),
    )
    assert decision.allowed is False
    assert decision.status == "DEFERRED"
    assert decision.resume_at == _dt(17, 0).astimezone(timezone.utc)


def test_begin_run_is_deferred_while_draining(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json")
    gate.drain(engine="claude", reason="switch", now=_dt(12))
    decision = gate.begin_run(task_uuid="task-1", engine="claude", now=_dt(12, 1))
    assert decision.allowed is False
    assert decision.status == "DEFERRED"
    snapshot = gate.snapshot(now=_dt(12, 2))
    assert "task-1" in snapshot.deferred_tasks
    assert "task-1" not in snapshot.running_tasks


def test_begin_run_registers_running_and_finish_run_clears_it(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json")
    decision = gate.begin_run(task_uuid="task-1", engine="claude", now=_dt(12))
    assert decision.allowed is True
    assert gate.snapshot().running_tasks["task-1"].engine == "claude"
    assert gate.finish_run(task_uuid="task-1") is True
    assert gate.finish_run(task_uuid="task-1") is False
    assert "task-1" not in gate.snapshot().running_tasks


def test_resume_releases_only_after_all_blockers_removed(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json")
    gate.report(engine="claude", status="paused", observed_at=_dt(12), reason="quota paused")
    gate.drain(engine="claude", reason="model switch", now=_dt(12, 1))
    gate.admit(task_uuid="task-1", engine="claude", phase="enqueue", now=_dt(12, 2))

    released = gate.resume(engine="claude", now=_dt(12, 3))
    assert released == []
    assert "task-1" in gate.snapshot().deferred_tasks

    clear_result = gate.clear(engine="claude", observed_at=_dt(12, 4))
    assert clear_result.applied is True
    assert "task-1" not in gate.snapshot().deferred_tasks


def test_stale_available_report_is_ignored(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json")
    gate.report(engine="claude", status="paused", observed_at=_dt(12, 0), resume_at=_dt(17, 0))
    gate.admit(task_uuid="task-1", engine="claude", phase="launch", now=_dt(12, 10))
    stale = gate.report(engine="claude", status="available", observed_at=_dt(11, 59))
    assert stale.stale_ignored is True

    snapshot = gate.snapshot(now=_dt(13, 0))
    assert "task-1" in snapshot.deferred_tasks
    assert snapshot.engines["claude"].status == "paused"


def test_release_ready_unblocks_only_target_engine(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json")
    gate.report(engine="claude", status="paused", observed_at=_dt(12), resume_at=_dt(17))
    gate.report(engine="codex", status="paused", observed_at=_dt(12), resume_at=_dt(18))
    gate.admit(task_uuid="claude-task", engine="claude", phase="launch", now=_dt(12, 5))
    gate.admit(task_uuid="codex-task", engine="codex", phase="launch", now=_dt(12, 5))

    released = gate.release_ready(now=_dt(17, 0))
    assert released == ["claude-task"]
    assert "codex-task" in gate.snapshot(now=_dt(17, 1)).deferred_tasks


def test_paused_without_resume_at_stays_deferred(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json")
    gate.report(engine="claude", status="paused", observed_at=_dt(12))
    gate.admit(task_uuid="task-1", engine="claude", phase="launch", now=_dt(12, 30))
    assert gate.release_ready(now=_dt(20, 0)) == []
    assert "task-1" in gate.snapshot(now=_dt(20, 0)).deferred_tasks

    gate.clear(engine="claude", observed_at=_dt(20, 1))
    assert "task-1" not in gate.snapshot(now=_dt(20, 2)).deferred_tasks


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


def test_parallel_report_and_admit_keeps_valid_json(tmp_path: Path) -> None:
    state_path = tmp_path / "quota-gate.json"
    gate = QuotaGate(state_path)

    def worker(engine_name: str, task_prefix: str) -> None:
        gate.report(engine=engine_name, status="paused", observed_at=_dt(12), resume_at=_dt(17))
        for idx in range(20):
            gate.admit(
                task_uuid=f"{task_prefix}-{idx}",
                engine=engine_name,
                phase="launch",
                now=_dt(12, 10),
            )

    t1 = threading.Thread(target=worker, args=("claude", "c"), daemon=True)
    t2 = threading.Thread(target=worker, args=("codex", "x"), daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    content = state_path.read_text(encoding="utf-8")
    payload = json.loads(content)
    assert payload["schema_version"] == 1
    assert len(payload["deferred_tasks"]) == 40


def test_wait_idle_returns_true_when_no_running(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json")
    assert gate.wait_idle("claude", timeout=0.1, poll_interval=0.01) is True


def test_wait_idle_returns_false_on_timeout(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json")
    gate.begin_run(task_uuid="task-1", engine="claude", now=_dt(12))
    assert gate.wait_idle("claude", timeout=0.05, poll_interval=0.01) is False


def test_wait_idle_validates_arguments(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json")
    with pytest.raises(ValueError):
        gate.wait_idle("claude", timeout=-1)
    with pytest.raises(ValueError):
        gate.wait_idle("claude", poll_interval=0)


def test_wait_idle_becomes_true_after_finish(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json")
    gate.begin_run(task_uuid="task-1", engine="claude", now=_dt(12))

    def _finish_later() -> None:
        time.sleep(0.03)
        gate.finish_run(task_uuid="task-1")

    thread = threading.Thread(target=_finish_later, daemon=True)
    thread.start()
    assert gate.wait_idle("claude", timeout=0.3, poll_interval=0.01) is True
    thread.join()


def test_legacy_state_without_new_keys_is_loaded(tmp_path: Path) -> None:
    state_path = tmp_path / "quota-gate.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "engines": {},
                "deferred_tasks": {},
            }
        ),
        encoding="utf-8",
    )
    gate = QuotaGate(state_path)
    snapshot = gate.snapshot()
    assert snapshot.draining_engines == {}
    assert snapshot.running_tasks == {}


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
        worker.join(timeout=1)
        assert done.is_set()

        snapshot = gate.snapshot(now=_dt(12, 2))
        in_running = task_uuid in snapshot.running_tasks
        in_deferred = task_uuid in snapshot.deferred_tasks
        assert in_running ^ in_deferred
        gate.finish_run(task_uuid=task_uuid)


def test_corrupted_state_is_fail_closed(tmp_path: Path) -> None:
    state_path = tmp_path / "quota-gate.json"
    state_path.write_text("{broken", encoding="utf-8")
    gate = QuotaGate(state_path, audit_path=tmp_path / "audit.jsonl")
    with pytest.raises(ValueError):
        gate.snapshot()
    with pytest.raises(ValueError):
        gate.admit(task_uuid="task-1", engine="claude", phase="launch", now=_dt(12))
