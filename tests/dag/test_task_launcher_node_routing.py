"""Tests for TaskLauncher remote node routing."""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ghdag.core.vocabulary import DONE_UNKNOWN_FAILURE
from ghdag.dag.circuit_breaker import CircuitBreakerPolicy
from ghdag.dag.models import DagConfig, Task
from ghdag.dag.task_launcher import TaskLauncher
from ghdag.io.done import is_done, read_done_content


def _nodes_yaml(tmp_path: Path, *, max_parallel: int = 1) -> Path:
    path = tmp_path / "nodes.yml"
    path.write_text(
        "nodes:\n"
        "  - name: build-1\n"
        "    labels: [linux]\n"
        "    ssh: build-1\n"
        "    workdir: /srv/ghdag\n"
        f"    max_parallel: {max_parallel}\n",
        encoding="utf-8",
    )
    return path


def _make_launcher(tmp_path: Path, *, nodes_path: Path | None = None) -> TaskLauncher:
    done = tmp_path / "jobs" / "done"
    done.mkdir(parents=True)
    (tmp_path / "jobs" / "running").mkdir(parents=True)
    config = DagConfig(
        exec_jsonl_path=tmp_path / "jobs" / "exec.jsonl",
        exec_done_dir=done,
        nodes_path=nodes_path,
    )
    return TaskLauncher(
        config,
        MagicMock(),
        CircuitBreakerPolicy(float("inf"), 2**31),
        MagicMock(),
        lambda _: None,
    )


@patch("ghdag.dag._process_host.subprocess.Popen")
def test_task_without_node_label_runs_locally(
    mock_popen: MagicMock,
    tmp_path: Path,
) -> None:
    """Refs sumipan/ghdag#7651 — tasks without node_label stay on LocalProcessHost."""
    proc = MagicMock()
    proc.pid = 99
    proc.stderr = io.BytesIO()
    proc.stdout = io.BytesIO()
    mock_popen.return_value = proc
    launcher = _make_launcher(tmp_path)
    task = Task(uuid="local-1", command="echo hi")
    assert launcher.launch("local-1", task) is True
    rt = launcher._running["local-1"]
    assert rt.node is None
    running = json.loads(
        (tmp_path / "jobs" / "running" / "local-1.json").read_text(encoding="utf-8")
    )
    assert "node" not in running


def test_task_with_missing_label_fails_with_reason(tmp_path: Path) -> None:
    """Refs sumipan/ghdag#7651 — unknown node_label fails with diagnostic."""
    launcher = _make_launcher(tmp_path)
    result_path = tmp_path / "jobs" / "result.md"
    task = Task(
        uuid="gpu-1",
        command="echo hi",
        result_path=str(result_path),
        annotations={"node_label": "gpu"},
    )
    hooks = launcher._hooks
    assert launcher.launch("gpu-1", task) is False
    assert result_path.read_text(encoding="utf-8") == "ghdag: no node has label 'gpu'\n"
    assert read_done_content(tmp_path / "jobs" / "done", "gpu-1") == DONE_UNKNOWN_FAILURE
    hooks.on_task_failure.assert_called_once()
    args = hooks.on_task_failure.call_args[0]
    assert "gpu" in args[3]


@patch("ghdag.dag._process_host.subprocess.Popen")
def test_task_with_full_nodes_is_not_launched(
    mock_popen: MagicMock,
    tmp_path: Path,
) -> None:
    """Refs sumipan/ghdag#7651 — full node capacity defers launch without done marker."""
    proc = MagicMock()
    proc.pid = 11
    proc.stderr = io.BytesIO()
    proc.stdout = io.BytesIO()
    mock_popen.return_value = proc
    nodes_path = _nodes_yaml(tmp_path, max_parallel=1)
    launcher = _make_launcher(tmp_path, nodes_path=nodes_path)
    first = Task(
        uuid="busy-1",
        command="echo one",
        annotations={"node_label": "linux"},
    )
    assert launcher.launch("busy-1", first) is True
    second = Task(
        uuid="busy-2",
        command="echo two",
        annotations={"node_label": "linux"},
    )
    assert launcher.launch("busy-2", second) is False
    assert not is_done(tmp_path / "jobs" / "done", "busy-2")


@patch("ghdag.dag._process_host.subprocess.Popen")
def test_running_file_records_node(
    mock_popen: MagicMock,
    tmp_path: Path,
) -> None:
    """Refs sumipan/ghdag#7651 — running file records node when set."""
    proc = MagicMock()
    proc.pid = 22
    proc.poll.return_value = None
    mock_popen.return_value = proc
    launcher = _make_launcher(tmp_path, nodes_path=_nodes_yaml(tmp_path))
    launcher._write_running_file(
        "rec-1",
        proc,
        Task(uuid="rec-1", command="echo"),
        None,
        node="build-1",
    )
    with_node = json.loads(
        (tmp_path / "jobs" / "running" / "rec-1.json").read_text(encoding="utf-8")
    )
    assert with_node["node"] == "build-1"
    launcher._write_running_file(
        "rec-2",
        proc,
        Task(uuid="rec-2", command="echo"),
        None,
        node=None,
    )
    without_node = json.loads(
        (tmp_path / "jobs" / "running" / "rec-2.json").read_text(encoding="utf-8")
    )
    assert "node" not in without_node


def test_adopt_unknown_node_stops_local_group_with_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Refs sumipan/ghdag#7651 — unknown node in running file stops local group only."""
    launcher = _make_launcher(tmp_path)
    running = tmp_path / "jobs" / "running" / "orph-1.json"
    running.parent.mkdir(parents=True, exist_ok=True)
    running.write_text(
        json.dumps(
            {
                "pid": 77,
                "pgid": 77,
                "engine": "",
                "started_at": "2020-01-01T00:00:00+00:00",
                "interrupted_at": "2020-01-01T00:00:01+00:00",
                "node": "vanished-node",
            }
        ),
        encoding="utf-8",
    )
    tasks = {"orph-1": Task(uuid="orph-1", command="sleep 600")}
    launcher._config.kill_grace = 0
    with (
        patch.object(launcher._process_host, "group_exists", return_value=True),
        patch.object(launcher._process_host, "terminate_group") as mock_term,
        patch.object(launcher._process_host, "kill_group"),
        caplog.at_level(logging.WARNING, logger="ghdag.dag.task_launcher"),
    ):
        launcher.adopt_orphans(tasks)
    mock_term.assert_called_once_with(77)
    assert any("unknown node" in r.message and "vanished-node" in r.message for r in caplog.records)
