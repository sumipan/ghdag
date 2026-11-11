"""G8: TaskLauncher.check_completions() integration tests for TaskMetrics."""

from __future__ import annotations

import io
from unittest.mock import patch

import pytest

from ghdag.core.models.metrics import FailureClass, TaskMetrics
from ghdag.dag.engine import DagEngine
from ghdag.dag.hooks import DefaultHooks
from ghdag.dag.models import DagConfig, Task, _RunningProcess

FIXED_WALL = 1_700_000_000.0
FIXED_MONO = 1_000.0


class _FakeTaskClock:
    def __init__(self) -> None:
        self._mono = FIXED_MONO
        self._wall = FIXED_WALL

    def monotonic(self) -> float:
        return self._mono

    def time(self) -> float:
        return self._wall


@pytest.fixture
def fake_task_clock(monkeypatch: pytest.MonkeyPatch) -> _FakeTaskClock:
    clock = _FakeTaskClock()
    monkeypatch.setattr("ghdag.dag.task_launcher.time", clock)
    return clock


def make_config(tmp_path):
    (tmp_path / "done").mkdir(parents=True, exist_ok=True)
    return DagConfig(
        exec_jsonl_path=str(tmp_path / "exec.jsonl"),
        exec_done_dir=str(tmp_path / "done"),
    )


class MemoryHooks(DefaultHooks):
    def __init__(self) -> None:
        super().__init__()
        self.rejected = False
        self.pipeline_status = None
        self.successes: list[tuple] = []
        self.failures: list[tuple] = []
        self.rejected_events: list[tuple] = []
        self.empty_results: list[tuple] = []

    def check_rejected(self, result_path: str) -> bool:
        return self.rejected

    def check_pipeline_status(self, result_path: str):
        return self.pipeline_status

    def on_task_success(self, uuid, task, metrics):
        self.successes.append((uuid, task, metrics))

    def on_task_failure(self, uuid, task, returncode, stderr_text, metrics):
        self.failures.append((uuid, task, returncode, stderr_text, metrics))

    def on_task_rejected(self, uuid, task, retry_depth, is_final, metrics):
        self.rejected_events.append((uuid, task, retry_depth, is_final, metrics))

    def on_task_empty_result(self, uuid, task, stderr_text, metrics):
        self.empty_results.append((uuid, task, stderr_text, metrics))


def make_running_task(uuid="test-uuid", command="echo hello", returncode=0, stderr=b"", retry=0):
    from unittest.mock import MagicMock

    proc = MagicMock()
    proc.poll.return_value = returncode
    proc.returncode = returncode
    task = Task(uuid=uuid, command=command, retry=retry)
    return _RunningProcess(
        uuid=uuid,
        task=task,
        proc=proc,
        started_at=FIXED_WALL - 0.1,
        started_at_mono=FIXED_MONO - 0.1,
        stderr_buf=io.BytesIO(stderr),
        retry_depth=retry,
    )


def make_engine(tmp_path):
    hooks = MemoryHooks()
    engine = DagEngine(make_config(tmp_path), hooks)
    return engine, hooks


@patch("ghdag.dag.task_launcher._extract_tee_target", return_value=None)
def test_success_path(mock_tee, tmp_path, fake_task_clock):
    engine, hooks = make_engine(tmp_path)
    rt = make_running_task()
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    assert len(hooks.successes) == 1
    uuid_arg, task_arg, metrics = hooks.successes[0]
    assert isinstance(metrics, TaskMetrics)
    assert metrics.status == "success"
    assert isinstance(metrics.wall_time_sec, float)
    assert metrics.wall_time_sec > 0
    assert metrics.uuid == rt.uuid
    assert metrics.failure_class is None


@patch("ghdag.dag.task_launcher._extract_tee_target", return_value=None)
def test_failure_path(mock_tee, tmp_path, fake_task_clock):
    engine, hooks = make_engine(tmp_path)
    rt = make_running_task(returncode=1)
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    assert len(hooks.failures) == 1
    _, _, _, _, metrics = hooks.failures[0]
    assert isinstance(metrics, TaskMetrics)
    assert metrics.status == "failure"
    assert metrics.failure_class == FailureClass.PROCESS_ERROR


@patch("ghdag.dag.task_launcher._extract_tee_target", return_value="result.md")
def test_rejected_path(mock_tee, tmp_path, fake_task_clock):
    engine, hooks = make_engine(tmp_path)
    hooks.rejected = True
    rt = make_running_task()
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    assert len(hooks.rejected_events) == 1
    _, _, _, _, metrics = hooks.rejected_events[0]
    assert isinstance(metrics, TaskMetrics)
    assert metrics.status == "rejected"
    assert metrics.failure_class == FailureClass.REJECTED


def test_empty_result_path(tmp_path, fake_task_clock):
    engine, hooks = make_engine(tmp_path)
    result_file = tmp_path / "result.md"
    result_file.write_text("")

    rt = make_running_task()
    engine._launcher._running[rt.uuid] = rt

    with patch("ghdag.dag.task_launcher._extract_tee_target", return_value=str(result_file)):
        engine._launcher.check_completions()

    assert len(hooks.empty_results) == 1
    _, _, _, metrics = hooks.empty_results[0]
    assert isinstance(metrics, TaskMetrics)
    assert metrics.status == "empty_result"
    assert metrics.failure_class == FailureClass.EMPTY_RESULT


@patch("ghdag.dag.task_launcher._extract_tee_target", return_value=None)
def test_engine_model_in_metrics(mock_tee, tmp_path, fake_task_clock):
    engine, hooks = make_engine(tmp_path)
    rt = make_running_task(command='claude -p "hello" --model claude-opus-4-6')
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    assert len(hooks.successes) == 1
    metrics = hooks.successes[0][2]
    assert metrics.engine == "claude"
    assert metrics.model == "claude-opus-4-6"


@patch("ghdag.dag.task_launcher._extract_tee_target", return_value=None)
def test_timeout_failure_class(mock_tee, tmp_path, fake_task_clock):
    """Timeout path → metrics.failure_class == \"TIMEOUT\"."""
    engine, hooks = make_engine(tmp_path)
    rt = make_running_task()
    rt.term_sent_at = FIXED_MONO - 1.0
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    assert len(hooks.failures) == 1
    assert hooks.failures[0][4].failure_class == FailureClass.TIMEOUT


@patch("ghdag.dag.task_launcher._extract_tee_target", return_value="result.md")
def test_pipeline_failed_failure_class(mock_tee, tmp_path, fake_task_clock):
    """PIPELINE_STATUS: *_FAILED path → metrics.failure_class == \"PIPELINE_FAILED\"."""
    engine, hooks = make_engine(tmp_path)
    hooks.pipeline_status = "IMPL_FAILED"
    rt = make_running_task()
    engine._launcher._running[rt.uuid] = rt

    engine._launcher.check_completions()

    assert len(hooks.failures) == 1
    assert hooks.failures[0][4].failure_class == FailureClass.PIPELINE_FAILED
