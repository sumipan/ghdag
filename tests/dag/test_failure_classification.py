from __future__ import annotations

import io
import logging
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from ghdag.config.language import STATE_IDS, UI_KEYS, get_language_pack
from ghdag.core.models.metrics import FailureClass
from ghdag.core.vocabulary import (
    DONE_ENGINE_ENV_ERROR,
    DONE_ENGINE_ERROR,
    DONE_ENGINE_ERROR_FINAL,
    DONE_INTERACTIVE_PROMPT,
)
from ghdag.dag.engine import DagEngine
from ghdag.dag.engine_quarantine import EngineQuarantine
from ghdag.dag.hooks import DefaultHooks
from ghdag.dag.models import DagConfig, Task, _RunningProcess
from ghdag.llm.adapters import get_output_adapter
from ghdag.llm.adapters.claude_json import ClaudeJsonAdapter
from ghdag.llm.adapters.codex import CodexAdapter
from ghdag.llm.adapters.codex_jsonl import CodexJsonlAdapter
from ghdag.llm.adapters.cursor_stream import CursorStreamAdapter
from ghdag.llm.adapters.failure_classification import looks_like_question

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


# Non-CJK suffix used to exercise ``question_suffixes.extra`` (U+203D INTERROBANG).
_EXTRA_SUFFIX = "\u203d"


def _load_fixture(name: str) -> bytes:
    return (_FIXTURES / name).read_bytes()


@pytest.fixture(autouse=True)
def _isolate_language_pack(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Run every test with the EN pack unless a test sets its own pack."""
    monkeypatch.delenv("GHDAG_LANGUAGE_PACK", raising=False)
    get_language_pack.cache_clear()
    yield
    get_language_pack.cache_clear()


@pytest.fixture
def extra_suffix_pack(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Load a pack whose ``question_suffixes.extra`` holds :data:`_EXTRA_SUFFIX`."""
    data = {
        "state_labels": {k: f"S-{k}" for k in STATE_IDS},
        "ui": {k: f"U-{k}" for k in UI_KEYS},
        "question_suffixes": {"extra": [_EXTRA_SUFFIX]},
    }
    path = tmp_path / "pack.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("GHDAG_LANGUAGE_PACK", str(path))
    get_language_pack.cache_clear()


def _make_config(tmp_path):
    (tmp_path / "done").mkdir(parents=True, exist_ok=True)
    return DagConfig(
        exec_jsonl_path=str(tmp_path / "exec.jsonl"),
        exec_done_dir=str(tmp_path / "done"),
    )


def _done_value(tmp_path, uuid: str) -> str:
    return (tmp_path / "done" / uuid).read_text(encoding="utf-8").strip()


class MemoryHooks(DefaultHooks):
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


def _make_engine(tmp_path):
    hooks = MemoryHooks()
    return DagEngine(_make_config(tmp_path), hooks), hooks


def _make_running_task(task: Task, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 1) -> _RunningProcess:
    proc = MagicMock()
    proc.poll.return_value = returncode
    proc.returncode = returncode
    return _RunningProcess(
        uuid=task.uuid,
        task=task,
        proc=proc,
        started_at=1_700_000_000.0 - 0.1,
        started_at_mono=10_000.0 - 0.1,
        stderr_buf=io.BytesIO(stderr),
        stdout_buf=io.BytesIO(stdout),
        retry_depth=task.retry,
    )


@pytest.fixture
def all_adapters():
    return [
        ClaudeJsonAdapter(),
        CodexAdapter(),
    ]


def test_passthrough_classify_failure_default_none() -> None:
    assert get_output_adapter("unknown").classify_failure(1, b"", b"boom") is None


def test_all_adapters_return_none_for_ordinary_process_failure(all_adapters) -> None:
    for adapter in all_adapters:
        assert adapter.classify_failure(1, b"ordinary process failure", b"") is None


def test_all_adapters_handle_invalid_utf8_without_exception(all_adapters) -> None:
    noisy_bytes = b"\xff\xfe\xfa\xfd"
    for adapter in all_adapters:
        assert adapter.classify_failure(1, noisy_bytes, b"") is None


def test_engine_error_from_stderr_sets_retryable_marker(tmp_path) -> None:
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

    assert _done_value(tmp_path, task.uuid) == DONE_ENGINE_ERROR
    assert len(hooks.failures) == 1
    assert hooks.failures[0][4].failure_class == FailureClass.ENGINE_ERROR


def test_engine_error_from_stderr_honors_max_retry(tmp_path) -> None:
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

    assert _done_value(tmp_path, task.uuid) == DONE_ENGINE_ERROR_FINAL
    assert len(hooks.failures) == 1
    assert hooks.failures[0][4].failure_class == FailureClass.ENGINE_ERROR


def test_runtime_quota_failure_from_stderr_is_deferred(tmp_path) -> None:
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

    assert not hooks.failures
    assert not (tmp_path / "done" / task.uuid).exists()
    assert not result_file.exists()
    snapshot = engine._quota_gate.snapshot()
    assert snapshot.engines["claude"].status == "paused"


def test_runtime_auth_failure_keeps_auth_class_without_retry_gate_actions(tmp_path) -> None:
    engine, hooks = _make_engine(tmp_path)
    task = Task(
        uuid="runtime-auth",
        command="cursor agent -p hi",
        engine="cursor",
    )
    rt = _make_running_task(
        task,
        stdout=b"Failed to authenticate: OAuth session expired and could not be refreshed",
        stderr=b"",
        returncode=1,
    )
    engine._launcher._running[task.uuid] = rt
    engine._quota_gate.begin_run(task_uuid=task.uuid, engine="cursor")

    engine._launcher.check_completions()

    assert _done_value(tmp_path, task.uuid) == "1"
    assert len(hooks.failures) == 1
    assert hooks.failures[0][4].failure_class == FailureClass.AUTH
    snapshot = engine._quota_gate.snapshot()
    assert "cursor" not in snapshot.engines


@patch("ghdag.dag.task_launcher.subprocess.Popen")
def test_environment_error_enters_quarantine_and_blocks_same_engine(mock_popen, tmp_path) -> None:
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

    assert _done_value(tmp_path, task.uuid) == DONE_ENGINE_ENV_ERROR
    assert len(hooks.failures) == 1
    assert hooks.failures[0][4].failure_class == FailureClass.ENGINE_ENVIRONMENT_ERROR

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


def test_looks_like_question_without_pack_ignores_extra_suffix() -> None:
    assert looks_like_question(f"Choose an option{_EXTRA_SUFFIX}") is False


def test_classify_failure_pack_load_failure_still_detects_ascii_question(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("GHDAG_LANGUAGE_PACK", str(tmp_path / "missing.yaml"))
    stdout = _load_fixture("claude_json_interactive_prompt.json")
    with caplog.at_level(logging.WARNING):
        result = ClaudeJsonAdapter().classify_failure(1, stdout, b"")
    assert result == FailureClass.INTERACTIVE_PROMPT
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_failure_class_interactive_prompt_meta() -> None:
    assert FailureClass.INTERACTIVE_PROMPT.value == "INTERACTIVE_PROMPT"
    assert FailureClass.INTERACTIVE_PROMPT.cause == "permanent"
    assert FailureClass.INTERACTIVE_PROMPT.retry_policy == "forbidden"


_EXTRA_SUFFIX_CASES = [
    (ClaudeJsonAdapter(), "claude_json_interactive_prompt_extra_suffix.json"),
    (CursorStreamAdapter(), "cursor_stream_interactive_prompt_extra_suffix.jsonl"),
    (CodexJsonlAdapter(), "codex_jsonl_interactive_prompt_extra_suffix.jsonl"),
    (CodexAdapter(), "codex_jsonl_interactive_prompt_extra_suffix.jsonl"),
]


@pytest.mark.parametrize(("adapter", "fixture"), _EXTRA_SUFFIX_CASES)
def test_adapters_extra_suffix_with_pack_is_interactive_prompt(
    adapter, fixture: str, extra_suffix_pack: None
) -> None:
    """Pack ``question_suffixes.extra`` endings count as questions on exit != 0 only."""
    stdout = _load_fixture(fixture)
    assert adapter.classify_failure(1, stdout, b"") == FailureClass.INTERACTIVE_PROMPT
    assert adapter.classify_failure(0, stdout, b"") is None


def test_interactive_prompt_exit1_marks_done_without_retry(tmp_path) -> None:
    engine, hooks = _make_engine(tmp_path)
    task = Task(uuid="interactive-exit1", command="codex exec -p hi", engine="codex", retry=0)
    stdout = _load_fixture("codex_jsonl_interactive_prompt.jsonl")
    rt = _make_running_task(task, stdout=stdout, stderr=b"", returncode=1)
    engine._launcher._running[task.uuid] = rt
    engine._quota_gate.begin_run(task_uuid=task.uuid, engine="codex")

    engine._launcher.check_completions()

    assert _done_value(tmp_path, task.uuid) == DONE_INTERACTIVE_PROMPT
    assert len(hooks.failures) == 1
    metrics = hooks.failures[0][4]
    question_excerpt = hooks.failures[0][3]
    assert metrics.failure_class == FailureClass.INTERACTIVE_PROMPT
    assert metrics.failure_class_reason == "Please choose one?"
    assert len(question_excerpt) <= 200
    assert "Please choose one?" in question_excerpt or "bugbot" in question_excerpt


def test_cursor_retriable_error_sets_engine_error_marker(tmp_path) -> None:
    engine, hooks = _make_engine(tmp_path)
    stderr = _load_fixture("cursor_stream_retriable_error_reconnect.stderr")
    task = Task(uuid="cursor-retryable", command="cursor agent -p hi", engine="cursor", retry=0)
    rt = _make_running_task(task, stdout=b"", stderr=stderr, returncode=1)
    engine._launcher._running[task.uuid] = rt
    engine._quota_gate.begin_run(task_uuid=task.uuid, engine="cursor")

    engine._launcher.check_completions()

    assert _done_value(tmp_path, task.uuid) == DONE_ENGINE_ERROR
    assert len(hooks.failures) == 1
    assert hooks.failures[0][4].failure_class == FailureClass.ENGINE_ERROR
    error_summary = hooks.failures[0][3]
    assert "ENGINE_ERROR (RATE_LIMIT): RetriableError: [resource_exhausted] Error" in error_summary


