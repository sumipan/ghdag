"""Tests for PROCESS_ERROR/TIMEOUT stdout persistence to {result_path}.fail."""

from __future__ import annotations

import io
import time
from unittest.mock import MagicMock, patch

from ghdag.dag.engine import DagEngine
from ghdag.dag.hooks import DagHooks
from ghdag.dag.models import DagConfig, RunningTask, Task


def _make_config(tmp_path):
    return DagConfig(
        exec_jsonl_path=str(tmp_path / "exec.jsonl"),
        exec_done_dir=str(tmp_path / "done"),
    )


def _make_running_task(
    tmp_path,
    *,
    uuid: str = "test-uuid",
    returncode: int = 0,
    stdout: bytes = b"",
    result_path: str | None = None,
    term_sent_at: float | None = None,
):
    proc = MagicMock()
    proc.poll.return_value = returncode
    proc.returncode = returncode
    task = Task(
        uuid=uuid,
        command="echo hello",
        result_path=result_path,
    )
    rt = RunningTask(
        uuid=uuid,
        task=task,
        proc=proc,
        started_at=time.time() - 0.1,
        started_at_mono=time.monotonic() - 0.1,
        stderr_buf=io.BytesIO(b""),
        retry_depth=0,
        stdout_buf=io.BytesIO(stdout) if stdout or result_path is not None else None,
        term_sent_at=term_sent_at,
    )
    return rt


def _make_engine(tmp_path):
    hooks = MagicMock(spec=DagHooks)
    hooks.check_rejected.return_value = False
    hooks.check_pipeline_status.return_value = None
    engine = DagEngine(_make_config(tmp_path), hooks)
    return engine, hooks


@patch("ghdag.dag.engine.state_mark_done")
def test_process_error_writes_fail_file_when_stdout_non_empty(mock_mark_done, tmp_path):
    engine, hooks = _make_engine(tmp_path)
    result_file = tmp_path / "result.md"
    stdout_data = b"partial output from failed process"
    rt = _make_running_task(
        tmp_path,
        returncode=1,
        stdout=stdout_data,
        result_path=str(result_file),
    )
    engine._running[rt.uuid] = rt

    engine._check_completions()

    fail_path = tmp_path / "result.md.fail"
    assert fail_path.exists()
    assert fail_path.read_bytes() == stdout_data
    hooks.on_task_failure.assert_called_once()


@patch("ghdag.dag.engine.state_mark_done")
def test_process_error_skips_fail_file_when_stdout_empty(mock_mark_done, tmp_path):
    engine, hooks = _make_engine(tmp_path)
    result_file = tmp_path / "result.md"
    rt = _make_running_task(
        tmp_path,
        returncode=1,
        stdout=b"",
        result_path=str(result_file),
    )
    engine._running[rt.uuid] = rt

    engine._check_completions()

    assert not (tmp_path / "result.md.fail").exists()
    hooks.on_task_failure.assert_called_once()


@patch("ghdag.dag.engine.state_mark_done")
@patch("ghdag.dag.engine._extract_tee_target", return_value=None)
def test_timeout_writes_fail_file_when_stdout_non_empty(mock_tee, mock_mark_done, tmp_path):
    engine, hooks = _make_engine(tmp_path)
    result_file = tmp_path / "result.md"
    stdout_data = b"output before timeout"
    rt = _make_running_task(
        tmp_path,
        stdout=stdout_data,
        result_path=str(result_file),
        term_sent_at=time.monotonic() - 1.0,
    )
    engine._running[rt.uuid] = rt

    engine._check_completions()

    fail_path = tmp_path / "result.md.fail"
    assert fail_path.exists()
    assert fail_path.read_bytes() == stdout_data
    hooks.on_task_failure.assert_called_once()


@patch("ghdag.dag.engine.state_mark_done")
@patch("ghdag.dag.engine._extract_tee_target", return_value=None)
def test_success_does_not_write_fail_file(mock_tee, mock_mark_done, tmp_path):
    engine, hooks = _make_engine(tmp_path)
    result_file = tmp_path / "result.md"
    stdout_data = b"successful output"
    rt = _make_running_task(
        tmp_path,
        returncode=0,
        stdout=stdout_data,
        result_path=str(result_file),
    )
    engine._running[rt.uuid] = rt

    engine._check_completions()

    assert not (tmp_path / "result.md.fail").exists()
    assert result_file.read_bytes() == stdout_data
    hooks.on_task_success.assert_called_once()
