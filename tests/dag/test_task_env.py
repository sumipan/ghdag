"""Launched tasks receive their identity via GHDAG_TASK_UUID / GHDAG_RESULT_PATH (sumipan/nexus#3515)."""
from __future__ import annotations

import io
import os
from unittest.mock import MagicMock, patch

from ghdag.dag.circuit_breaker import CircuitBreakerPolicy
from ghdag.dag.models import DagConfig, Task
from ghdag.dag.task_launcher import TaskLauncher, _task_env


def test_task_env_adds_identity_and_keeps_parent_env(monkeypatch):
    monkeypatch.delenv("GHDAG_TASK_UUID", raising=False)
    monkeypatch.setenv("PARENT_MARKER", "kept")
    task = Task(uuid="u-1", command="true", engine="shell", model="bash", result_path="/tmp/r.md")
    env = _task_env("u-1", task)
    assert env["GHDAG_TASK_UUID"] == "u-1"
    assert env["GHDAG_RESULT_PATH"] == "/tmp/r.md"
    assert env["PARENT_MARKER"] == "kept"
    assert "GHDAG_TASK_UUID" not in os.environ


def test_task_env_without_result_path_is_empty_string():
    task = Task(uuid="u-2", command="true", engine="shell", model="bash", result_path=None)
    assert _task_env("u-2", task)["GHDAG_RESULT_PATH"] == ""


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
def test_launch_passes_task_env_to_child(mock_popen, tmp_path, monkeypatch) -> None:
    """TaskLauncher.launch passes _task_env(...) as env= to subprocess.Popen."""
    monkeypatch.setenv("PARENT_MARKER", "kept")
    proc = MagicMock()
    proc.pid = 99
    proc.stderr = io.BytesIO(b"")
    mock_popen.return_value = proc
    launcher = _make_launcher(tmp_path)
    task = Task(
        uuid="u-launch",
        command="echo hi",
        engine="shell",
        result_path="/tmp/out.md",
    )
    assert launcher.launch("u-launch", task) is True
    assert mock_popen.called
    env = mock_popen.call_args.kwargs["env"]
    assert env["GHDAG_TASK_UUID"] == "u-launch"
    assert env["GHDAG_RESULT_PATH"] == "/tmp/out.md"
    assert env["PARENT_MARKER"] == "kept"
