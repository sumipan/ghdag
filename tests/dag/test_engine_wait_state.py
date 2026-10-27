"""WAIT_QUOTA protocol, engine pause, and legacy migration in DagEngine."""

from __future__ import annotations

import io
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from ghdag.core.vocabulary import (
    DONE_PIPELINE_FAILED_PREFIX,
    PIPELINE_LEGACY_DEFERRED_UNSUPPORTED,
    PIPELINE_WAIT_QUOTA,
    PIPELINE_WAIT_QUOTA_INVALID,
)
from ghdag.dag._util import parse_wait_quota_signal
from ghdag.dag.engine import DagEngine
from ghdag.dag.models import DagConfig, RunningTask, Task
from ghdag.llm.engines import list_engines

JST = timezone(timedelta(hours=9))


def _make_engine(tmp_path: Path):
    exec_path = tmp_path / "jobs" / "exec.jsonl"
    done_dir = tmp_path / "jobs" / "done"
    done_dir.mkdir(parents=True, exist_ok=True)
    exec_path.write_text(
        json.dumps({"uuid": "task-1", "command": "claude -p hi", "depends": []}) + "\n",
        encoding="utf-8",
    )
    config = DagConfig(exec_jsonl_path=exec_path, exec_done_dir=done_dir, poll_interval=0.01)
    hooks = MagicMock()
    hooks.check_rejected.return_value = False
    hooks.check_pipeline_status.side_effect = lambda path: _read_pipeline_status(path)
    return DagEngine(config, hooks), done_dir, config.quota_audit_path


def _read_pipeline_status(path: str) -> str | None:
    from ghdag.dag._util import check_pipeline_status

    return check_pipeline_status(path)


def test_parse_wait_quota_signal_accepts_companion() -> None:
    content = (
        "PIPELINE_STATUS: WAIT_QUOTA\n"
        "WAIT_QUOTA: after=2026-10-01T12:00:00+09:00 engines=claude,codex\n"
    )
    parsed = parse_wait_quota_signal(content, known_engines=frozenset(list_engines()))
    assert parsed.engines == ("claude", "codex")
    assert parsed.after is not None


@patch("ghdag.dag.task_launcher.state_mark_done")
def test_wait_quota_deletes_result_and_records_engine_pause(mock_mark_done, tmp_path: Path) -> None:
    engine, done_dir, audit_path = _make_engine(tmp_path)
    result_file = tmp_path / "result.md"
    result_file.write_text(
        "PIPELINE_STATUS: WAIT_QUOTA\n"
        "WAIT_QUOTA: after= engines=claude\n",
        encoding="utf-8",
    )
    task = Task(uuid="task-1", command="claude -p hi", engine="claude", result_path=str(result_file))
    proc = MagicMock()
    proc.poll.return_value = 0
    proc.returncode = 0
    engine._launcher._running[task.uuid] = RunningTask(
        uuid=task.uuid,
        task=task,
        proc=proc,
        started_at=time.time(),
        started_at_mono=time.monotonic(),
        stderr_buf=io.BytesIO(b""),
        stdout_buf=io.BytesIO(b""),
    )
    engine._quota_gate.begin_run(task_uuid=task.uuid, engine="claude")

    engine._launcher.check_completions()

    mock_mark_done.assert_not_called()
    assert not result_file.exists()
    assert engine._quota_gate.snapshot().engines["claude"].status == "paused"
    audit_text = audit_path.read_text(encoding="utf-8")
    assert "task_waiting" in audit_text


@patch("ghdag.dag.task_launcher.state_mark_done")
def test_invalid_wait_quota_marks_pipeline_failed(mock_mark_done, tmp_path: Path) -> None:
    engine, _, _ = _make_engine(tmp_path)
    result_file = tmp_path / "bad.md"
    result_file.write_text("PIPELINE_STATUS: WAIT_QUOTA\n", encoding="utf-8")
    task = Task(uuid="task-1", command="claude -p hi", engine="claude", result_path=str(result_file))
    proc = MagicMock()
    proc.poll.return_value = 0
    proc.returncode = 0
    engine._launcher._running[task.uuid] = RunningTask(
        uuid=task.uuid,
        task=task,
        proc=proc,
        started_at=time.time(),
        started_at_mono=time.monotonic(),
        stderr_buf=io.BytesIO(b""),
        stdout_buf=io.BytesIO(b""),
    )

    engine._launcher.check_completions()

    expected = f"{DONE_PIPELINE_FAILED_PREFIX}{PIPELINE_WAIT_QUOTA_INVALID}"
    mock_mark_done.assert_called_once_with(engine._config.exec_done_dir, task.uuid, expected)


def test_engine_restarts_waiting_task_when_engine_available(tmp_path: Path) -> None:
    engine, done_dir, _ = _make_engine(tmp_path)
    past = datetime(2026, 9, 22, 11, tzinfo=JST)
    engine._quota_gate.report(
        engine="claude",
        status="paused",
        observed_at=past,
        resume_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )

    def stop_after_launch(*_args, **_kwargs):
        engine._shutdown = True

    proc = MagicMock()
    proc.poll.return_value = None
    with patch("ghdag.dag.task_launcher.subprocess.Popen", return_value=proc) as mock_popen, patch(
        "ghdag.dag.engine.time.sleep", side_effect=stop_after_launch
    ):
        engine._quota_gate.clear(engine="claude", observed_at=datetime(2026, 9, 22, 12, tzinfo=JST))
        engine.run()

    assert not (done_dir / "task-1").exists()
    mock_popen.assert_called()


def test_legacy_deferred_done_marker_migrated_on_load(tmp_path: Path) -> None:
    engine, done_dir, audit_path = _make_engine(tmp_path)
    (done_dir / "task-1").write_text("DEFERRED", encoding="utf-8")

    def stop(*_a, **_k):
        engine._shutdown = True

    with patch("ghdag.dag.engine.time.sleep", side_effect=stop), patch(
        "ghdag.dag.task_launcher.subprocess.Popen"
    ):
        engine.run()

    assert not (done_dir / "task-1").exists()
    assert "task_deferred_migrated" in audit_path.read_text(encoding="utf-8")
