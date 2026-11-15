"""Tests for TaskLauncher.check_completions() failure result handling."""

from __future__ import annotations

import io
import time
from unittest.mock import MagicMock

from ghdag.core.models.metrics import FailureClass
from ghdag.core.vocabulary import (
    DONE_ENGINE_ERROR,
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


