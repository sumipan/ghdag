"""Tests for TaskLauncher.check_completions() failure result handling."""

from __future__ import annotations

import io
import time
from unittest.mock import MagicMock, patch

from ghdag.core.vocabulary import DONE_DEFERRED, DONE_ENGINE_ERROR, DONE_ENGINE_ERROR_FINAL
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


@patch("ghdag.dag.task_launcher.state_mark_done")
def test_pipeline_status_deferred_marks_done_deferred_no_failure_hook(mock_mark_done, tmp_path):
    engine, hooks = _make_engine(tmp_path)
    result_file = tmp_path / "result.md"
    result_file.write_text("PIPELINE_STATUS: DEFERRED\n", encoding="utf-8")

    hooks.check_pipeline_status.return_value = "DEFERRED"

    proc = MagicMock()
    proc.poll.return_value = 0
    proc.returncode = 0
    task = Task(uuid="deferred-1", command="claude -p hi", engine="claude", result_path=str(result_file))
    rt = RunningTask(
        uuid=task.uuid,
        task=task,
        proc=proc,
        started_at=time.time() - 0.1,
        started_at_mono=time.monotonic() - 0.1,
        stderr_buf=io.BytesIO(b""),
        stdout_buf=io.BytesIO(b""),
        retry_depth=0,
    )
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    mock_mark_done.assert_called_once_with(engine._config.exec_done_dir, task.uuid, DONE_DEFERRED)
    hooks.on_task_failure.assert_not_called()


@patch("ghdag.dag.task_launcher.state_mark_done")
def test_pipeline_status_deferred_circuit_breaker_not_triggered(mock_mark_done, tmp_path):
    engine, hooks = _make_engine(tmp_path)
    result_file = tmp_path / "result.md"
    result_file.write_text("PIPELINE_STATUS: DEFERRED\n", encoding="utf-8")

    hooks.check_pipeline_status.return_value = "DEFERRED"

    proc = MagicMock()
    proc.poll.return_value = 0
    proc.returncode = 0
    task = Task(uuid="deferred-2", command="claude -p hi", engine="claude", result_path=str(result_file))
    rt = RunningTask(
        uuid=task.uuid,
        task=task,
        proc=proc,
        started_at=time.time() - 0.1,
        started_at_mono=time.monotonic() - 0.1,
        stderr_buf=io.BytesIO(b""),
        stdout_buf=io.BytesIO(b""),
        retry_depth=0,
    )
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    assert not engine._launcher._circuit_breaker.tripped


def test_pipeline_status_deferred_downstream_does_not_start(tmp_path):
    exec_path = tmp_path / "jobs" / "exec.jsonl"
    done_dir = tmp_path / "jobs" / "done"
    done_dir.mkdir(parents=True, exist_ok=True)

    import json as _json

    from ghdag.dag.engine import DagEngine as _DagEngine

    exec_path.parent.mkdir(parents=True, exist_ok=True)
    exec_path.write_text(
        _json.dumps({"uuid": "upstream", "command": "echo up", "depends": []}) + "\n"
        + _json.dumps({"uuid": "downstream", "command": "echo down", "depends": ["upstream"]}) + "\n",
        encoding="utf-8",
    )

    hooks = MagicMock()
    hooks.check_rejected.return_value = False
    hooks.check_pipeline_status.return_value = None

    config = DagConfig(exec_jsonl_path=exec_path, exec_done_dir=done_dir, poll_interval=0.01)
    engine = _DagEngine(config, hooks)

    (done_dir / "upstream").write_text(DONE_DEFERRED, encoding="utf-8")

    loop_count = 0

    def stop_after_two_sleeps(*_args, **_kwargs):
        nonlocal loop_count
        loop_count += 1
        if loop_count >= 2:
            engine._shutdown = True

    with patch("ghdag.dag.task_launcher.subprocess.Popen") as mock_popen, patch(
        "ghdag.dag.engine.time.sleep", side_effect=stop_after_two_sleeps
    ):
        engine.run()

    mock_popen.assert_not_called()
    assert not (done_dir / "downstream").exists()


def _make_shell_running_task(uuid, returncode, stdout, result_path, stderr=b""):
    proc = MagicMock()
    proc.poll.return_value = returncode
    proc.returncode = returncode
    task = Task(uuid=uuid, command="echo boom; exit 1", engine="shell", result_path=str(result_path))
    return RunningTask(
        uuid=uuid,
        task=task,
        proc=proc,
        started_at=time.time() - 0.1,
        started_at_mono=time.monotonic() - 0.1,
        stderr_buf=io.BytesIO(stderr),
        stdout_buf=io.BytesIO(stdout),
        retry_depth=0,
    )


@patch("ghdag.dag.task_launcher.state_mark_done")
def test_shell_failure_writes_stdout_and_exit_code_to_result(mock_mark_done, tmp_path):
    engine, hooks = _make_engine(tmp_path)
    result_file = tmp_path / "result.md"
    rt = _make_shell_running_task("shell-fail", 1, b"boom\n", result_file)
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    assert result_file.read_text(encoding="utf-8") == "boom\nEXIT_CODE: 1\n"
    mock_mark_done.assert_called_once_with(engine._config.exec_done_dir, rt.uuid, 1)
    hooks.on_task_failure.assert_called_once()
    hooks.on_task_success.assert_not_called()


@patch("ghdag.dag.task_launcher.state_mark_done")
def test_shell_failure_without_trailing_newline(mock_mark_done, tmp_path):
    engine, hooks = _make_engine(tmp_path)
    result_file = tmp_path / "result.md"
    rt = _make_shell_running_task("shell-fail-nonl", 2, b"boom", result_file)
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    assert result_file.read_text(encoding="utf-8") == "boom\nEXIT_CODE: 2\n"


@patch("ghdag.dag.task_launcher.state_mark_done")
def test_shell_failure_empty_stdout_writes_exit_code_only(mock_mark_done, tmp_path):
    engine, hooks = _make_engine(tmp_path)
    result_file = tmp_path / "result.md"
    rt = _make_shell_running_task("shell-fail-empty", 1, b"", result_file)
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    assert result_file.read_text(encoding="utf-8") == "EXIT_CODE: 1\n"


@patch("ghdag.dag.task_launcher.state_mark_done")
def test_shell_success_result_has_no_exit_code(mock_mark_done, tmp_path):
    engine, hooks = _make_engine(tmp_path)
    result_file = tmp_path / "result.md"
    rt = _make_shell_running_task("shell-ok", 0, b"ok\n", result_file)
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    assert result_file.read_text(encoding="utf-8") == "ok\n"
    mock_mark_done.assert_called_once_with(engine._config.exec_done_dir, rt.uuid, 0)


@patch("ghdag.dag.task_launcher.state_mark_done")
def test_non_shell_failure_does_not_write_result(mock_mark_done, tmp_path):
    engine, hooks = _make_engine(tmp_path)
    result_file = tmp_path / "result.md"
    proc = MagicMock()
    proc.poll.return_value = 1
    proc.returncode = 1
    task = Task(uuid="claude-fail", command="claude -p hi", engine="claude", result_path=str(result_file))
    rt = RunningTask(
        uuid=task.uuid,
        task=task,
        proc=proc,
        started_at=time.time() - 0.1,
        started_at_mono=time.monotonic() - 0.1,
        stderr_buf=io.BytesIO(b"err"),
        stdout_buf=io.BytesIO(b""),
        retry_depth=0,
    )
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    assert not result_file.exists()


def test_shell_failure_result_does_not_count_as_succeeded(tmp_path):
    from ghdag.io.done import load_succeeded_from_dir

    engine, hooks = _make_engine(tmp_path)
    result_file = tmp_path / "result.md"
    rt = _make_shell_running_task("shell-fail-done", 1, b"boom\n", result_file)
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    assert result_file.exists()
    done_file = tmp_path / "done" / rt.uuid
    assert done_file.read_text(encoding="utf-8").strip() == "1"
    assert rt.uuid not in load_succeeded_from_dir(engine._config.exec_done_dir)
