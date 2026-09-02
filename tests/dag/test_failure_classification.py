from __future__ import annotations

import io
import json
import time
from unittest.mock import MagicMock, patch

from ghdag.core.vocabulary import (
    DONE_ENGINE_ENV_ERROR,
    DONE_ENGINE_ERROR,
    DONE_ENGINE_ERROR_FINAL,
)
from ghdag.dag.engine import DagEngine
from ghdag.dag.engine_quarantine import EngineQuarantine
from ghdag.dag.hooks import DagHooks
from ghdag.dag.models import DagConfig, RunningTask, Task
from ghdag.llm.adapters import get_output_adapter
from ghdag.llm.adapters.claude_json import ClaudeJsonAdapter
from ghdag.llm.adapters.codex import CodexAdapter
from ghdag.llm.adapters.cursor import CursorAdapter
from ghdag.metrics.models import FailureClass


def _make_config(tmp_path):
    return DagConfig(
        exec_jsonl_path=str(tmp_path / "exec.jsonl"),
        exec_done_dir=str(tmp_path / "done"),
    )


def _make_engine(tmp_path):
    hooks = MagicMock(spec=DagHooks)
    hooks.check_rejected.return_value = False
    hooks.check_pipeline_status.return_value = None
    return DagEngine(_make_config(tmp_path), hooks), hooks


def _make_running_task(task: Task, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 1) -> RunningTask:
    proc = MagicMock()
    proc.poll.return_value = returncode
    proc.returncode = returncode
    return RunningTask(
        uuid=task.uuid,
        task=task,
        proc=proc,
        started_at=time.time() - 0.1,
        started_at_mono=time.monotonic() - 0.1,
        stderr_buf=io.BytesIO(stderr),
        stdout_buf=io.BytesIO(stdout),
        retry_depth=task.retry,
    )


def test_passthrough_and_cursor_classify_failure_default_none() -> None:
    assert get_output_adapter("unknown").classify_failure(1, b"", b"boom") is None
    assert CursorAdapter().classify_failure(1, b"", b"boom") is None


def test_codex_classify_failure_detects_engine_error_from_stderr() -> None:
    adapter = CodexAdapter()
    failure = adapter.classify_failure(
        1,
        b"",
        b"codex_models_manager::manager: failed to load models cache: missing field",
    )
    assert failure == FailureClass.ENGINE_ERROR


def test_codex_classify_failure_detects_environment_error() -> None:
    adapter = CodexAdapter()
    failure = adapter.classify_failure(
        127,
        b"",
        b"codex: No such file or directory",
    )
    assert failure == FailureClass.ENGINE_ENVIRONMENT_ERROR


def test_claude_classify_failure_detects_quota_exhausted_from_stderr() -> None:
    adapter = ClaudeJsonAdapter()
    failure = adapter.classify_failure(
        1,
        b"",
        b"You've hit your session limit \xc2\xb7 resets 2:20am",
    )
    assert failure == FailureClass.QUOTA_EXHAUSTED


@patch("ghdag.dag.task_launcher.state_mark_done")
def test_engine_error_from_stderr_sets_retryable_marker(mock_mark_done, tmp_path) -> None:
    engine, hooks = _make_engine(tmp_path)
    task = Task(uuid="retryable", command="codex exec -p hi", engine="codex", retry=0)
    rt = _make_running_task(
        task,
        stdout=b"",
        stderr=b"failed to load models cache",
        returncode=1,
    )
    engine._launcher._running[task.uuid] = rt
    engine._quota_gate.begin_run(task_uuid=task.uuid, engine="codex")

    engine._launcher.check_completions()

    mock_mark_done.assert_called_once_with(engine._config.exec_done_dir, task.uuid, DONE_ENGINE_ERROR)
    hooks.on_task_failure.assert_called_once()
    metrics = hooks.on_task_failure.call_args[0][4]
    assert metrics.failure_class == FailureClass.ENGINE_ERROR


@patch("ghdag.dag.task_launcher.state_mark_done")
def test_engine_error_from_stderr_honors_max_retry(mock_mark_done, tmp_path) -> None:
    engine, hooks = _make_engine(tmp_path)
    task = Task(
        uuid="retry-final",
        command="codex exec -p hi",
        engine="codex",
        retry=engine._config.max_retry,
    )
    rt = _make_running_task(
        task,
        stdout=b"",
        stderr=b"failed to load models cache",
        returncode=1,
    )
    engine._launcher._running[task.uuid] = rt
    engine._quota_gate.begin_run(task_uuid=task.uuid, engine="codex")

    engine._launcher.check_completions()

    mock_mark_done.assert_called_once_with(engine._config.exec_done_dir, task.uuid, DONE_ENGINE_ERROR_FINAL)
    hooks.on_task_failure.assert_called_once()
    metrics = hooks.on_task_failure.call_args[0][4]
    assert metrics.failure_class == FailureClass.ENGINE_ERROR


@patch("ghdag.dag.task_launcher.state_mark_done")
def test_runtime_quota_failure_from_stderr_is_deferred(mock_mark_done, tmp_path) -> None:
    engine, hooks = _make_engine(tmp_path)
    result_file = tmp_path / "result.md"
    result_file.write_text("placeholder", encoding="utf-8")
    task = Task(
        uuid="runtime-quota",
        command="claude -p hi",
        engine="claude",
        result_path=str(result_file),
    )
    rt = _make_running_task(
        task,
        stdout=b"",
        stderr=b"You've hit your session limit \xc2\xb7 resets 2:20am",
        returncode=1,
    )
    engine._launcher._running[task.uuid] = rt
    engine._quota_gate.begin_run(task_uuid=task.uuid, engine="claude")

    engine._launcher.check_completions()

    hooks.on_task_failure.assert_not_called()
    mock_mark_done.assert_not_called()
    assert not result_file.exists()
    snapshot = engine._quota_gate.snapshot()
    assert task.uuid in snapshot.deferred_tasks
    assert snapshot.deferred_tasks[task.uuid].phase == "runtime"


@patch("ghdag.dag.task_launcher.state_mark_done")
@patch("ghdag.dag.task_launcher.subprocess.Popen")
def test_environment_error_enters_quarantine_and_blocks_same_engine(mock_popen, mock_mark_done, tmp_path) -> None:
    engine, hooks = _make_engine(tmp_path)
    task = Task(uuid="env-fail", command="claude -p hi", engine="claude")
    rt = _make_running_task(
        task,
        stdout=b"",
        stderr=b"claude: No such file or directory",
        returncode=127,
    )
    engine._launcher._running[task.uuid] = rt
    engine._quota_gate.begin_run(task_uuid=task.uuid, engine="claude")

    engine._launcher.check_completions()

    mock_mark_done.assert_called_once_with(engine._config.exec_done_dir, task.uuid, DONE_ENGINE_ENV_ERROR)
    hooks.on_task_failure.assert_called_once()
    metrics = hooks.on_task_failure.call_args[0][4]
    assert metrics.failure_class == FailureClass.ENGINE_ENVIRONMENT_ERROR

    blocked_task = Task(uuid="blocked", command="claude -p blocked", engine="claude")
    assert engine._launcher.launch(blocked_task.uuid, blocked_task) is False

    other_task = Task(uuid="other", command="codex exec -p ok", engine="codex")
    dummy_proc = MagicMock()
    dummy_proc.stderr = io.BytesIO(b"")
    mock_popen.return_value = dummy_proc
    assert engine._launcher.launch(other_task.uuid, other_task) is True


def test_engine_quarantine_expires_after_cooldown() -> None:
    q = EngineQuarantine()
    with patch("ghdag.dag.engine_quarantine.time.monotonic", return_value=100.0):
        q.enter("claude", cooldown=10)
        assert q.is_quarantined("claude") is True
    with patch("ghdag.dag.engine_quarantine.time.monotonic", return_value=111.0):
        assert q.is_quarantined("claude") is False


@patch("ghdag.dag.task_launcher.state_mark_done")
def test_retry_and_quarantine_audit_events_are_written(mock_mark_done, tmp_path) -> None:
    engine, _hooks = _make_engine(tmp_path)
    queue_dir = tmp_path
    audit_path = queue_dir / "audit.jsonl"

    retry_task = Task(uuid="retry-audit", command="codex exec -p hi", engine="codex", retry=0)
    retry_rt = _make_running_task(
        retry_task,
        stdout=b"",
        stderr=b"failed to load models cache",
        returncode=1,
    )
    engine._launcher._running[retry_task.uuid] = retry_rt
    engine._quota_gate.begin_run(task_uuid=retry_task.uuid, engine="codex")
    engine._launcher.check_completions()

    env_task = Task(uuid="env-audit", command="claude -p hi", engine="claude")
    env_rt = _make_running_task(
        env_task,
        stdout=b"",
        stderr=b"claude: Permission denied",
        returncode=126,
    )
    engine._launcher._running[env_task.uuid] = env_rt
    engine._quota_gate.begin_run(task_uuid=env_task.uuid, engine="claude")
    engine._launcher.check_completions()

    events = [
        json.loads(line)["event_type"]
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert "task_retry" in events
    assert "engine_quarantine" in events
