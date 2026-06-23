"""G8: TaskLauncher.check_completions() integration tests for TaskMetrics."""

from __future__ import annotations

import io
import time
from unittest.mock import MagicMock, patch

from ghdag.dag.engine import DagEngine
from ghdag.dag.hooks import DagHooks
from ghdag.dag.models import DagConfig, RunningTask, Task
from ghdag.metrics.models import FailureClass, TaskMetrics


def make_config(tmp_path):
    return DagConfig(
        exec_jsonl_path=str(tmp_path / "exec.jsonl"),
        exec_done_dir=str(tmp_path / "done"),
    )


def make_running_task(uuid="test-uuid", command="echo hello", returncode=0, stderr=b"", retry=0):
    proc = MagicMock()
    proc.poll.return_value = returncode
    proc.returncode = returncode
    task = Task(uuid=uuid, command=command, retry=retry)
    return RunningTask(
        uuid=uuid,
        task=task,
        proc=proc,
        started_at=time.time() - 0.1,
        started_at_mono=time.monotonic() - 0.1,
        stderr_buf=io.BytesIO(stderr),
        retry_depth=retry,
    )


def make_engine(tmp_path):
    hooks = MagicMock(spec=DagHooks)
    hooks.check_rejected.return_value = False
    hooks.check_pipeline_status.return_value = None
    engine = DagEngine(make_config(tmp_path), hooks)
    return engine, hooks


@patch("ghdag.dag.task_launcher.state_mark_done")
@patch("ghdag.dag.task_launcher._extract_tee_target", return_value=None)
def test_success_path(mock_tee, mock_mark_done, tmp_path):
    engine, hooks = make_engine(tmp_path)
    rt = make_running_task()
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    hooks.on_task_success.assert_called_once()
    uuid_arg, task_arg, metrics = hooks.on_task_success.call_args[0]
    assert isinstance(metrics, TaskMetrics)
    assert metrics.status == "success"
    assert isinstance(metrics.wall_time_sec, float)
    assert metrics.wall_time_sec > 0
    assert metrics.uuid == rt.uuid
    assert metrics.failure_class is None


@patch("ghdag.dag.task_launcher.state_mark_done")
@patch("ghdag.dag.task_launcher._extract_tee_target", return_value=None)
def test_failure_path(mock_tee, mock_mark_done, tmp_path):
    engine, hooks = make_engine(tmp_path)
    rt = make_running_task(returncode=1)
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    hooks.on_task_failure.assert_called_once()
    uuid_arg, task_arg, returncode_arg, stderr_arg, metrics = hooks.on_task_failure.call_args[0]
    assert isinstance(metrics, TaskMetrics)
    assert metrics.status == "failure"
    assert metrics.failure_class == FailureClass.PROCESS_ERROR


@patch("ghdag.dag.task_launcher.state_mark_done")
@patch("ghdag.dag.task_launcher._extract_tee_target", return_value="result.md")
def test_rejected_path(mock_tee, mock_mark_done, tmp_path):
    engine, hooks = make_engine(tmp_path)
    hooks.check_rejected.return_value = True
    rt = make_running_task()
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    hooks.on_task_rejected.assert_called_once()
    uuid_arg, task_arg, retry_depth_arg, is_final_arg, metrics = hooks.on_task_rejected.call_args[0]
    assert isinstance(metrics, TaskMetrics)
    assert metrics.status == "rejected"
    assert metrics.failure_class == FailureClass.REJECTED


@patch("ghdag.dag.task_launcher.state_mark_done")
def test_empty_result_path(mock_mark_done, tmp_path):
    engine, hooks = make_engine(tmp_path)
    result_file = tmp_path / "result.md"
    result_file.write_text("")

    rt = make_running_task()
    engine._launcher._running[rt.uuid] = rt

    with patch("ghdag.dag.task_launcher._extract_tee_target", return_value=str(result_file)):
        engine._launcher.check_completions()

    hooks.on_task_empty_result.assert_called_once()
    uuid_arg, task_arg, stderr_arg, metrics = hooks.on_task_empty_result.call_args[0]
    assert isinstance(metrics, TaskMetrics)
    assert metrics.status == "empty_result"
    assert metrics.failure_class == FailureClass.EMPTY_RESULT


@patch("ghdag.dag.task_launcher.state_mark_done")
@patch("ghdag.dag.task_launcher._extract_tee_target", return_value=None)
def test_engine_model_in_metrics(mock_tee, mock_mark_done, tmp_path):
    engine, hooks = make_engine(tmp_path)
    rt = make_running_task(command='claude -p "hello" --model claude-opus-4-6')
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    hooks.on_task_success.assert_called_once()
    uuid_arg, task_arg, metrics = hooks.on_task_success.call_args[0]
    assert metrics.engine == "claude"
    assert metrics.model == "claude-opus-4-6"


# --- Issue #962 tests ---

@patch("ghdag.dag.task_launcher.state_mark_done")
@patch("ghdag.dag.task_launcher._extract_tee_target", return_value=None)
def test_timeout_failure_class(mock_tee, mock_mark_done, tmp_path):
    """タイムアウトパス → metrics.failure_class == "TIMEOUT"。"""
    engine, hooks = make_engine(tmp_path)
    rt = make_running_task()
    rt.term_sent_at = time.monotonic() - 1.0
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    hooks.on_task_failure.assert_called_once()
    _, _, _, _, metrics = hooks.on_task_failure.call_args[0]
    assert metrics.failure_class == FailureClass.TIMEOUT


@patch("ghdag.dag.task_launcher.state_mark_done")
@patch("ghdag.dag.task_launcher._extract_tee_target", return_value="result.md")
def test_pipeline_failed_failure_class(mock_tee, mock_mark_done, tmp_path):
    """PIPELINE_STATUS: *_FAILED パス → metrics.failure_class == "PIPELINE_FAILED"。"""
    engine, hooks = make_engine(tmp_path)
    hooks.check_rejected.return_value = False
    hooks.check_pipeline_status.return_value = "IMPL_FAILED"
    rt = make_running_task()
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    hooks.on_task_failure.assert_called_once()
    _, _, _, _, metrics = hooks.on_task_failure.call_args[0]
    assert metrics.failure_class == FailureClass.PIPELINE_FAILED
