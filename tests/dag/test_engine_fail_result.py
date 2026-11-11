"""Tests for TaskLauncher.check_completions() failure result handling."""

from __future__ import annotations

import io
import time
from unittest.mock import MagicMock, patch

from ghdag.core.models.metrics import FailureClass
from ghdag.core.vocabulary import (
    DONE_ENGINE_ERROR,
    DONE_ENGINE_ERROR_FINAL,
    DONE_PIPELINE_FAILED_PREFIX,
    LEGACY_DONE_DEFERRED,
    PIPELINE_LEGACY_DEFERRED_UNSUPPORTED,
)
from ghdag.dag.engine import DagEngine
from ghdag.dag.hooks import DefaultHooks
from ghdag.dag.models import DagConfig, Task, _RunningProcess


def _make_config(tmp_path):
    (tmp_path / "done").mkdir(parents=True, exist_ok=True)
    return DagConfig(
        exec_jsonl_path=str(tmp_path / "exec.jsonl"),
        exec_done_dir=str(tmp_path / "done"),
    )


def _done_value(tmp_path, uuid: str) -> str:
    return (tmp_path / "done" / uuid).read_text(encoding="utf-8").strip()


class MemoryHooks(DefaultHooks):
    """Records hook outcomes for assertions without mock call introspection."""

    def __init__(self) -> None:
        super().__init__()
        self.rejected = False
        self.pipeline_status = None
        self.failures: list[tuple] = []
        self.successes: list[tuple] = []

    def check_rejected(self, result_path: str) -> bool:
        return self.rejected

    def check_pipeline_status(self, result_path: str):
        return self.pipeline_status

    def on_task_failure(self, uuid, task, returncode, stderr_text, metrics):
        self.failures.append((uuid, task, returncode, stderr_text, metrics))

    def on_task_success(self, uuid, task, metrics):
        self.successes.append((uuid, task, metrics))


def _make_running_task(uuid="fail-uuid", returncode=1, stderr=b"some error"):
    proc = MagicMock()
    proc.poll.return_value = returncode
    proc.returncode = returncode
    task = Task(uuid=uuid, command="echo test")
    return _RunningProcess(
        uuid=uuid,
        task=task,
        proc=proc,
        started_at=time.time() - 0.1,
        started_at_mono=time.monotonic() - 0.1,
        stderr_buf=io.BytesIO(stderr),
        retry_depth=0,
    )


def _make_engine(tmp_path):
    hooks = MemoryHooks()
    engine = DagEngine(_make_config(tmp_path), hooks)
    return engine, hooks


@patch("ghdag.dag.task_launcher._extract_tee_target", return_value=None)
def test_nonzero_returncode_calls_on_task_failure(mock_tee, tmp_path):
    engine, hooks = _make_engine(tmp_path)
    rt = _make_running_task(returncode=2)
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    assert len(hooks.failures) == 1
    _, _, returncode_arg, _, metrics = hooks.failures[0]
    assert returncode_arg == 2
    assert metrics.failure_class == FailureClass.PROCESS_ERROR
    assert metrics.status == "failure"
    assert _done_value(tmp_path, rt.uuid) == "2"


@patch("ghdag.dag.task_launcher._extract_tee_target", return_value=None)
def test_stderr_text_passed_to_failure_hook(mock_tee, tmp_path):
    engine, hooks = _make_engine(tmp_path)
    rt = _make_running_task(returncode=1, stderr=b"fatal error occurred")
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    assert len(hooks.failures) == 1
    _, _, _, stderr_arg, _ = hooks.failures[0]
    assert "fatal error occurred" in stderr_arg


@patch("ghdag.dag.task_launcher._extract_tee_target", return_value=None)
def test_failure_marks_done_with_returncode(mock_tee, tmp_path):
    engine, hooks = _make_engine(tmp_path)
    rt = _make_running_task(returncode=3)
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    assert _done_value(tmp_path, rt.uuid) == "3"


@patch("ghdag.dag.task_launcher._extract_tee_target", return_value=None)
def test_task_removed_from_running_after_failure(mock_tee, tmp_path):
    engine, hooks = _make_engine(tmp_path)
    rt = _make_running_task(returncode=1)
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    assert rt.uuid not in engine._launcher._running


def test_retryable_engine_error_marks_done_for_retry(tmp_path):
    engine, hooks = _make_engine(tmp_path)
    result_file = tmp_path / "result.md"
    stdout = b'{"type":"error","message":"Selected model is at capacity."}\n'
    proc = MagicMock()
    proc.poll.return_value = 0
    proc.returncode = 0
    task = Task(uuid="err-retry", command="codex exec", engine="codex", result_path=str(result_file), retry=0)
    rt = _RunningProcess(
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

    assert _done_value(tmp_path, task.uuid) == DONE_ENGINE_ERROR
    assert len(hooks.failures) == 1
    _, _, returncode_arg, stderr_arg, metrics = hooks.failures[0]
    assert returncode_arg == 0
    assert "ENGINE_ERROR (CAPACITY)" in stderr_arg
    assert metrics.failure_class == FailureClass.ENGINE_ERROR
    assert result_file.read_text(encoding="utf-8").startswith("ENGINE_ERROR (CAPACITY):")


def test_non_retryable_engine_error_marks_done_final(tmp_path):
    engine, hooks = _make_engine(tmp_path)
    stdout = b'{"type":"error","message":"Authentication failed"}\n'
    proc = MagicMock()
    proc.poll.return_value = 0
    proc.returncode = 0
    task = Task(uuid="err-final", command="codex exec", engine="codex", retry=0)
    rt = _RunningProcess(
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

    assert _done_value(tmp_path, task.uuid) == DONE_ENGINE_ERROR_FINAL
    assert len(hooks.failures) == 1
    assert hooks.failures[0][4].failure_class == FailureClass.ENGINE_ERROR


def test_pipeline_status_legacy_deferred_is_unsupported_failure(tmp_path):
    engine, hooks = _make_engine(tmp_path)
    result_file = tmp_path / "result.md"
    result_file.write_text("PIPELINE_STATUS: DEFERRED\n", encoding="utf-8")

    hooks.pipeline_status = "DEFERRED"

    proc = MagicMock()
    proc.poll.return_value = 0
    proc.returncode = 0
    task = Task(uuid="deferred-1", command="claude -p hi", engine="claude", result_path=str(result_file))
    rt = _RunningProcess(
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

    expected = f"{DONE_PIPELINE_FAILED_PREFIX}{PIPELINE_LEGACY_DEFERRED_UNSUPPORTED}"
    assert _done_value(tmp_path, task.uuid) == expected
    assert len(hooks.failures) == 1


def test_pipeline_status_legacy_deferred_records_failure(tmp_path):
    engine, hooks = _make_engine(tmp_path)
    result_file = tmp_path / "result.md"
    result_file.write_text("PIPELINE_STATUS: DEFERRED\n", encoding="utf-8")

    hooks.pipeline_status = "DEFERRED"

    proc = MagicMock()
    proc.poll.return_value = 0
    proc.returncode = 0
    task = Task(uuid="deferred-2", command="claude -p hi", engine="claude", result_path=str(result_file))
    rt = _RunningProcess(
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

    assert len(hooks.failures) == 1


def test_legacy_deferred_done_migrated_downstream_waits(tmp_path):
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

    (done_dir / "upstream").write_text(LEGACY_DONE_DEFERRED, encoding="utf-8")

    loop_count = 0

    def stop_after_two_sleeps(*_args, **_kwargs):
        nonlocal loop_count
        loop_count += 1
        if loop_count >= 2:
            engine._shutdown = True

    launched: list[str] = []

    def track_popen(args, **_kwargs):
        launched.append(args[-1])
        engine._shutdown = True
        proc = MagicMock()
        proc.poll.return_value = None
        proc.stdout = io.BytesIO(b"")
        proc.stderr = io.BytesIO(b"")
        return proc

    with patch("ghdag.dag.task_launcher.subprocess.Popen", side_effect=track_popen), patch(
        "ghdag.dag.engine.time.sleep", side_effect=stop_after_two_sleeps
    ):
        engine.run()

    assert not (done_dir / "upstream").exists()
    assert launched == ["echo up"]
    assert not (done_dir / "downstream").exists()


def _make_shell_running_task(uuid, returncode, stdout, result_path, stderr=b""):
    proc = MagicMock()
    proc.poll.return_value = returncode
    proc.returncode = returncode
    task = Task(uuid=uuid, command="echo boom; exit 1", engine="shell", result_path=str(result_path))
    return _RunningProcess(
        uuid=uuid,
        task=task,
        proc=proc,
        started_at=time.time() - 0.1,
        started_at_mono=time.monotonic() - 0.1,
        stderr_buf=io.BytesIO(stderr),
        stdout_buf=io.BytesIO(stdout),
        retry_depth=0,
    )


def test_shell_failure_writes_stdout_and_exit_code_to_result(tmp_path):
    engine, hooks = _make_engine(tmp_path)
    result_file = tmp_path / "result.md"
    rt = _make_shell_running_task("shell-fail", 1, b"boom\n", result_file)
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    assert result_file.read_text(encoding="utf-8") == "boom\nEXIT_CODE: 1\n"
    assert _done_value(tmp_path, rt.uuid) == "1"
    assert len(hooks.failures) == 1
    assert not hooks.successes


def test_shell_failure_without_trailing_newline(tmp_path):
    engine, hooks = _make_engine(tmp_path)
    result_file = tmp_path / "result.md"
    rt = _make_shell_running_task("shell-fail-nonl", 2, b"boom", result_file)
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    assert result_file.read_text(encoding="utf-8") == "boom\nEXIT_CODE: 2\n"


def test_shell_failure_empty_stdout_writes_exit_code_only(tmp_path):
    engine, hooks = _make_engine(tmp_path)
    result_file = tmp_path / "result.md"
    rt = _make_shell_running_task("shell-fail-empty", 1, b"", result_file)
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    assert result_file.read_text(encoding="utf-8") == "EXIT_CODE: 1\n"


def test_shell_success_result_has_no_exit_code(tmp_path):
    engine, hooks = _make_engine(tmp_path)
    result_file = tmp_path / "result.md"
    rt = _make_shell_running_task("shell-ok", 0, b"ok\n", result_file)
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    assert result_file.read_text(encoding="utf-8") == "ok\n"
    assert _done_value(tmp_path, rt.uuid) == "0"


def test_non_shell_failure_does_not_write_result(tmp_path):
    engine, hooks = _make_engine(tmp_path)
    result_file = tmp_path / "result.md"
    proc = MagicMock()
    proc.poll.return_value = 1
    proc.returncode = 1
    task = Task(uuid="claude-fail", command="claude -p hi", engine="claude", result_path=str(result_file))
    rt = _RunningProcess(
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
