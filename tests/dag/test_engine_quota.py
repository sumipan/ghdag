from __future__ import annotations

import io
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from ghdag.dag.engine import DagEngine
from ghdag.dag.models import DagConfig, RunningTask, Task

JST = timezone(timedelta(hours=9))


def _write_exec(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_paused_launch_does_not_call_subprocess(tmp_path: Path) -> None:
    exec_path = tmp_path / "jobs" / "exec.jsonl"
    done_dir = tmp_path / "jobs" / "done"
    done_dir.mkdir(parents=True, exist_ok=True)
    _write_exec(exec_path, [{"uuid": "task-1", "command": "claude -p hello", "depends": []}])

    config = DagConfig(exec_jsonl_path=exec_path, exec_done_dir=done_dir, poll_interval=0.01)
    hooks = MagicMock()
    hooks.check_rejected.return_value = False
    hooks.check_pipeline_status.return_value = None
    engine = DagEngine(config, hooks)
    engine._quota_gate.report(
        engine="claude",
        status="paused",
        observed_at=datetime(2026, 9, 2, 12, 0, tzinfo=JST),
    )

    def stop_after_first_sleep(*_args, **_kwargs):
        engine._shutdown = True

    with patch.object(engine._launcher, "launch") as mock_launch, patch(
        "ghdag.dag.engine.time.sleep",
        side_effect=stop_after_first_sleep,
    ):
        engine.run()

    mock_launch.assert_not_called()


def test_enqueue_records_are_kept_and_deferred_registry_updated(tmp_path: Path) -> None:
    exec_path = tmp_path / "jobs" / "exec.jsonl"
    done_dir = tmp_path / "jobs" / "done"
    done_dir.mkdir(parents=True, exist_ok=True)
    _write_exec(exec_path, [])

    config = DagConfig(exec_jsonl_path=exec_path, exec_done_dir=done_dir)
    hooks = MagicMock()
    hooks.check_rejected.return_value = False
    hooks.check_pipeline_status.return_value = None
    engine = DagEngine(config, hooks)
    engine._quota_gate.report(
        engine="claude",
        status="paused",
        observed_at=datetime(2026, 9, 2, 12, 0, tzinfo=JST),
    )
    record = {"uuid": "task-1", "command": "claude -p hello", "engine": "claude", "depends": []}
    engine.append_task(json.dumps(record))

    text = exec_path.read_text(encoding="utf-8")
    assert '"uuid": "task-1"' in text
    snapshot = engine._quota_gate.snapshot()
    assert "task-1" in snapshot.deferred_tasks


@patch("ghdag.dag.task_launcher.state_mark_done")
def test_runtime_quota_error_becomes_deferred_without_failure_hook(mock_mark_done, tmp_path: Path) -> None:
    config = DagConfig(exec_jsonl_path=tmp_path / "exec.jsonl", exec_done_dir=tmp_path / "done")
    hooks = MagicMock()
    hooks.check_rejected.return_value = False
    hooks.check_pipeline_status.return_value = None
    engine = DagEngine(config, hooks)

    result_file = tmp_path / "result.md"
    result_file.write_text("placeholder", encoding="utf-8")
    stdout = (
        b'{"is_error":true,"error":{"message":"quota exhausted. '
        b'Try again at 2026-09-02T17:00:00+09:00"}}'
    )
    proc = MagicMock()
    proc.poll.return_value = 0
    proc.returncode = 0
    task = Task(uuid="runtime-1", command="claude -p hi", engine="claude", result_path=str(result_file))
    rt = RunningTask(
        uuid=task.uuid,
        task=task,
        proc=proc,
        started_at=time.time() - 0.1,
        started_at_mono=time.monotonic() - 0.1,
        stderr_buf=io.BytesIO(b""),
        stdout_buf=io.BytesIO(stdout),
    )
    engine._launcher._running[task.uuid] = rt

    engine._launcher.check_completions()

    hooks.on_task_failure.assert_not_called()
    assert not result_file.exists()
    mock_mark_done.assert_not_called()
    assert "runtime-1" in engine._quota_gate.snapshot().deferred_tasks
