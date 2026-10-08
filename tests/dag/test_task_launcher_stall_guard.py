"""TaskLauncher wiring for engine stall guard (issue #4931)."""

from __future__ import annotations

import io
import json
import signal
import time
from unittest.mock import MagicMock, patch

import pytest

from ghdag.dag.circuit_breaker import CircuitBreakerPolicy
from ghdag.dag.models import DagConfig, RunningTask, Task
from ghdag.dag.stall_guard import StallEvent, StallTracker
from ghdag.dag.task_launcher import TaskLauncher


def _make_launcher(tmp_path) -> TaskLauncher:
    done = tmp_path / "jobs" / "done"
    done.mkdir(parents=True, exist_ok=True)
    (tmp_path / "jobs").mkdir(parents=True, exist_ok=True)
    config = DagConfig(
        exec_jsonl_path=tmp_path / "jobs" / "exec.jsonl",
        exec_done_dir=done,
        task_timeout=3600.0,
        kill_grace=10.0,
    )
    return TaskLauncher(
        config,
        hooks=MagicMock(),
        circuit_breaker=CircuitBreakerPolicy(float("inf"), 2**31),
        fanout_manager=MagicMock(),
        promote_fn=MagicMock(),
    )


@patch("ghdag.dag.task_launcher.subprocess.Popen")
def test_cursor_launch_creates_stall_tracker(mock_popen, tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("GHDAG_ENGINE_STALL_GUARD", raising=False)
    proc = MagicMock()
    proc.pid = 4242
    proc.stderr = io.BytesIO(b"")
    mock_popen.return_value = proc
    launcher = _make_launcher(tmp_path)
    task = Task(uuid="u1", command="agent -p x", engine="cursor")
    assert launcher.launch("u1", task) is True
    rt = launcher._running["u1"]
    assert rt.stall_tracker is not None
    assert rt.stall_tracker._root_pid == 4242


@patch("ghdag.dag.task_launcher.subprocess.Popen")
def test_stall_guard_disabled(mock_popen, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GHDAG_ENGINE_STALL_GUARD", "0")
    proc = MagicMock()
    proc.pid = 1
    proc.stderr = io.BytesIO(b"")
    mock_popen.return_value = proc
    launcher = _make_launcher(tmp_path)
    task = Task(uuid="u1", command="agent -p x", engine="cursor")
    launcher.launch("u1", task)
    assert launcher._running["u1"].stall_tracker is None


@patch("ghdag.dag.task_launcher.subprocess.Popen")
@pytest.mark.parametrize("engine", ["claude", "codex"])
def test_non_cursor_no_tracker(mock_popen, tmp_path, engine: str) -> None:
    proc = MagicMock()
    proc.pid = 1
    proc.stderr = io.BytesIO(b"")
    mock_popen.return_value = proc
    launcher = _make_launcher(tmp_path)
    task = Task(uuid="u1", command="echo hi", engine=engine)
    launcher.launch("u1", task)
    assert launcher._running["u1"].stall_tracker is None


def test_check_completions_invokes_tracker_and_writes_audit(tmp_path, caplog) -> None:
    launcher = _make_launcher(tmp_path)
    proc = MagicMock()
    proc.poll.return_value = None
    task = Task(uuid="stall-uuid", command="agent", engine="cursor")
    mock_tracker = MagicMock(spec=StallTracker)
    mock_tracker.check.return_value = [
        StallEvent(pid=99, stalled_sec=180.0, parent_args_head="zsh -c snap"),
    ]
    rt = RunningTask(
        uuid="stall-uuid",
        task=task,
        proc=proc,
        started_at=time.time(),
        started_at_mono=time.monotonic(),
        stderr_buf=io.BytesIO(b""),
        stall_tracker=mock_tracker,
    )
    launcher._running["stall-uuid"] = rt

    with caplog.at_level("WARNING"):
        launcher.check_completions()

    mock_tracker.check.assert_called_once()
    assert any("[stall-guard]" in r.message for r in caplog.records)
    audit_path = tmp_path / "jobs" / "audit.jsonl"
    assert audit_path.exists()
    line = audit_path.read_text(encoding="utf-8").strip()
    record = json.loads(line)
    assert record["event_type"] == "stall_guard"
    assert record["task_uuid"] == "stall-uuid"
    assert record["pid"] == 99


@patch("ghdag.dag.task_launcher._signal_process_tree")
def test_timeout_still_signals_process_tree(mock_signal, tmp_path) -> None:
    launcher = _make_launcher(tmp_path)
    proc = MagicMock()
    proc.poll.return_value = None
    task = Task(uuid="t1", command="sleep", engine="cursor")
    rt = RunningTask(
        uuid="t1",
        task=task,
        proc=proc,
        started_at=time.time() - 5000,
        started_at_mono=time.monotonic() - 5000,
        stderr_buf=io.BytesIO(b""),
        stall_tracker=MagicMock(spec=StallTracker),
    )
    launcher._running["t1"] = rt
    launcher.check_completions()
    mock_signal.assert_called_once_with(proc, signal.SIGTERM)
