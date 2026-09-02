"""Tests for TaskLauncher.check_completions() failure result handling."""

from __future__ import annotations

import io
import time
from unittest.mock import MagicMock, patch

from ghdag.core.vocabulary import DONE_ENGINE_ERROR, DONE_ENGINE_ERROR_FINAL
from ghdag.dag.engine import DagEngine
from ghdag.dag.hooks import DagHooks
from ghdag.dag.models import DagConfig, RunningTask, Task
from ghdag.metrics.models import FailureClass


def _make_config(tmp_path):
    return DagConfig(
        exec_jsonl_path=str(tmp_path / "exec.jsonl"),
        exec_done_dir=str(tmp_path / "done"),
    )


def _make_running_task(uuid="fail-uuid", returncode=1, stderr=b"some error"):
    proc = MagicMock()
    proc.poll.return_value = returncode
    proc.returncode = returncode
    task = Task(uuid=uuid, command="echo test")
    return RunningTask(
        uuid=uuid,
        task=task,
        proc=proc,
        started_at=time.time() - 0.1,
        started_at_mono=time.monotonic() - 0.1,
        stderr_buf=io.BytesIO(stderr),
        retry_depth=0,
    )


def _make_engine(tmp_path):
    hooks = MagicMock(spec=DagHooks)
    hooks.check_rejected.return_value = False
    hooks.check_pipeline_status.return_value = None
    engine = DagEngine(_make_config(tmp_path), hooks)
    return engine, hooks


@patch("ghdag.dag.task_launcher.state_mark_done")
@patch("ghdag.dag.task_launcher._extract_tee_target", return_value=None)
def test_nonzero_returncode_calls_on_task_failure(mock_tee, mock_mark_done, tmp_path):
    engine, hooks = _make_engine(tmp_path)
    rt = _make_running_task(returncode=2)
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    hooks.on_task_failure.assert_called_once()
    _, _, returncode_arg, _, metrics = hooks.on_task_failure.call_args[0]
    assert returncode_arg == 2
    assert metrics.failure_class == FailureClass.PROCESS_ERROR
    assert metrics.status == "failure"


@patch("ghdag.dag.task_launcher.state_mark_done")
@patch("ghdag.dag.task_launcher._extract_tee_target", return_value=None)
def test_stderr_text_passed_to_failure_hook(mock_tee, mock_mark_done, tmp_path):
    engine, hooks = _make_engine(tmp_path)
    rt = _make_running_task(returncode=1, stderr=b"fatal error occurred")
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    hooks.on_task_failure.assert_called_once()
    _, _, _, stderr_arg, _ = hooks.on_task_failure.call_args[0]
    assert "fatal error occurred" in stderr_arg


@patch("ghdag.dag.task_launcher.state_mark_done")
@patch("ghdag.dag.task_launcher._extract_tee_target", return_value=None)
def test_failure_marks_done_with_returncode(mock_tee, mock_mark_done, tmp_path):
    engine, hooks = _make_engine(tmp_path)
    rt = _make_running_task(returncode=3)
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    mock_mark_done.assert_called_once_with(
        engine._config.exec_done_dir, rt.uuid, 3
    )


@patch("ghdag.dag.task_launcher.state_mark_done")
@patch("ghdag.dag.task_launcher._extract_tee_target", return_value=None)
def test_task_removed_from_running_after_failure(mock_tee, mock_mark_done, tmp_path):
    engine, hooks = _make_engine(tmp_path)
    rt = _make_running_task(returncode=1)
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    assert rt.uuid not in engine._launcher._running


@patch("ghdag.dag.task_launcher.state_mark_done")
def test_retryable_engine_error_marks_done_for_retry(mock_mark_done, tmp_path):
    engine, hooks = _make_engine(tmp_path)
    result_file = tmp_path / "result.md"
    stdout = b'{"type":"error","message":"Selected model is at capacity."}\n'
    proc = MagicMock()
    proc.poll.return_value = 0
    proc.returncode = 0
    task = Task(uuid="err-retry", command="codex exec", engine="codex", result_path=str(result_file), retry=0)
    rt = RunningTask(
        uuid=task.uuid,
        task=task,
        proc=proc,
        started_at=time.time() - 0.1,
        started_at_mono=time.monotonic() - 0.1,
        stderr_buf=io.BytesIO(b""),
        stdout_buf=io.BytesIO(stdout),
        retry_depth=0,
    )
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    mock_mark_done.assert_called_once_with(engine._config.exec_done_dir, task.uuid, DONE_ENGINE_ERROR)
    hooks.on_task_failure.assert_called_once()
    _, _, returncode_arg, stderr_arg, metrics = hooks.on_task_failure.call_args[0]
    assert returncode_arg == 0
    assert "ENGINE_ERROR (CAPACITY)" in stderr_arg
    assert metrics.failure_class == FailureClass.ENGINE_ERROR
    assert result_file.read_text(encoding="utf-8").startswith("ENGINE_ERROR (CAPACITY):")


@patch("ghdag.dag.task_launcher.state_mark_done")
def test_non_retryable_engine_error_marks_done_final(mock_mark_done, tmp_path):
    engine, hooks = _make_engine(tmp_path)
    stdout = b'{"type":"error","message":"Authentication failed"}\n'
    proc = MagicMock()
    proc.poll.return_value = 0
    proc.returncode = 0
    task = Task(uuid="err-final", command="codex exec", engine="codex", retry=0)
    rt = RunningTask(
        uuid=task.uuid,
        task=task,
        proc=proc,
        started_at=time.time() - 0.1,
        started_at_mono=time.monotonic() - 0.1,
        stderr_buf=io.BytesIO(b""),
        stdout_buf=io.BytesIO(stdout),
        retry_depth=0,
    )
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    mock_mark_done.assert_called_once_with(engine._config.exec_done_dir, task.uuid, DONE_ENGINE_ERROR_FINAL)
    hooks.on_task_failure.assert_called_once()
    metrics = hooks.on_task_failure.call_args[0][4]
    assert metrics.failure_class == FailureClass.ENGINE_ERROR
