"""Per-task timeout_sec annotation override tests (issue #2895)."""

from __future__ import annotations

import io
from unittest.mock import MagicMock

import pytest

from ghdag.dag.circuit_breaker import CircuitBreakerPolicy
from ghdag.dag.models import DagConfig, RunningTask, Task
from ghdag.dag.task_launcher import TaskLauncher, _resolve_task_timeout


class _FakeClock:
    def __init__(self, mono: float = 20_000.0, wall: float = 1_700_000_000.0) -> None:
        self._mono = mono
        self._wall = wall

    def monotonic(self) -> float:
        return self._mono

    def time(self) -> float:
        return self._wall

    def advance(self, sec: float) -> None:
        self._mono += sec
        self._wall += sec


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> _FakeClock:
    clock = _FakeClock()
    monkeypatch.setattr("ghdag.dag.task_launcher.time", clock)
    return clock


def _make_launcher(tmp_path, *, task_timeout: float | None = 600.0, kill_grace: float = 10.0) -> TaskLauncher:
    done = tmp_path / "jobs" / "done"
    done.mkdir(parents=True, exist_ok=True)
    config = DagConfig(
        exec_jsonl_path=tmp_path / "jobs" / "exec.jsonl",
        exec_done_dir=done,
        task_timeout=task_timeout,
        kill_grace=kill_grace,
    )
    return TaskLauncher(
        config,
        hooks=MagicMock(),
        circuit_breaker=CircuitBreakerPolicy(float("inf"), 2**31),
        fanout_manager=MagicMock(),
        promote_fn=MagicMock(),
    )


def _make_running_task(
    clock: _FakeClock,
    *,
    elapsed_sec: float,
    annotations: dict[str, str] | None = None,
    term_sent_at: float | None = None,
) -> RunningTask:
    proc = MagicMock()
    proc.poll.return_value = None
    task = Task(
        uuid="test-uuid",
        command="sleep 60",
        annotations=annotations or {},
    )
    return RunningTask(
        uuid="test-uuid",
        task=task,
        proc=proc,
        started_at=clock.time() - elapsed_sec,
        started_at_mono=clock.monotonic() - elapsed_sec,
        stderr_buf=io.BytesIO(b""),
        retry_depth=0,
        term_sent_at=term_sent_at,
    )


class TestResolveTaskTimeout:
    @pytest.mark.parametrize(
        ("annotations", "default", "expected"),
        [
            ({}, 600.0, 600.0),
            ({}, None, None),
            ({"timeout_sec": "1800"}, 600.0, 1800.0),
            ({"timeout_sec": "300"}, 600.0, 300.0),
            ({"timeout_sec": "abc"}, 600.0, 600.0),
            ({"timeout_sec": "0"}, 600.0, 600.0),
            ({"timeout_sec": "-10"}, 600.0, 600.0),
        ],
    )
    def test_resolve_task_timeout(
        self,
        annotations: dict[str, str],
        default: float | None,
        expected: float | None,
    ) -> None:
        task = Task(uuid="u", command="true", annotations=annotations)
        assert _resolve_task_timeout(task, default) == expected


class TestCheckCompletionsTimeoutOverride:
    def test_falls_back_to_global_timeout_without_annotation(self, tmp_path, fake_clock) -> None:
        launcher = _make_launcher(tmp_path, task_timeout=600.0)
        rt = _make_running_task(fake_clock, elapsed_sec=700.0)
        launcher._running[rt.uuid] = rt

        launcher.check_completions()

        rt.proc.terminate.assert_called_once()
        rt.proc.kill.assert_not_called()

    def test_no_timeout_when_global_none_and_no_annotation(self, tmp_path, fake_clock) -> None:
        launcher = _make_launcher(tmp_path, task_timeout=None)
        rt = _make_running_task(fake_clock, elapsed_sec=700.0)
        launcher._running[rt.uuid] = rt

        launcher.check_completions()

        rt.proc.terminate.assert_not_called()
        rt.proc.kill.assert_not_called()

    def test_annotation_1800_overrides_smaller_global(self, tmp_path, fake_clock) -> None:
        launcher = _make_launcher(tmp_path, task_timeout=600.0)
        rt = _make_running_task(fake_clock, elapsed_sec=700.0, annotations={"timeout_sec": "1800"})
        launcher._running[rt.uuid] = rt

        launcher.check_completions()

        rt.proc.terminate.assert_not_called()

    def test_annotation_1800_triggers_after_elapsed(self, tmp_path, fake_clock) -> None:
        launcher = _make_launcher(tmp_path, task_timeout=600.0)
        rt = _make_running_task(fake_clock, elapsed_sec=1900.0, annotations={"timeout_sec": "1800"})
        launcher._running[rt.uuid] = rt

        launcher.check_completions()

        rt.proc.terminate.assert_called_once()

    def test_annotation_300_overrides_larger_global(self, tmp_path, fake_clock) -> None:
        launcher = _make_launcher(tmp_path, task_timeout=600.0)
        rt = _make_running_task(fake_clock, elapsed_sec=400.0, annotations={"timeout_sec": "300"})
        launcher._running[rt.uuid] = rt

        launcher.check_completions()

        rt.proc.terminate.assert_called_once()

    def test_invalid_annotation_falls_back_to_global(self, tmp_path, fake_clock) -> None:
        launcher = _make_launcher(tmp_path, task_timeout=600.0)
        rt = _make_running_task(fake_clock, elapsed_sec=700.0, annotations={"timeout_sec": "abc"})
        launcher._running[rt.uuid] = rt

        launcher.check_completions()

        rt.proc.terminate.assert_called_once()

    def test_zero_annotation_falls_back_to_global(self, tmp_path, fake_clock) -> None:
        launcher = _make_launcher(tmp_path, task_timeout=600.0)
        rt = _make_running_task(fake_clock, elapsed_sec=700.0, annotations={"timeout_sec": "0"})
        launcher._running[rt.uuid] = rt

        launcher.check_completions()

        rt.proc.terminate.assert_called_once()

    def test_negative_annotation_falls_back_to_global(self, tmp_path, fake_clock) -> None:
        launcher = _make_launcher(tmp_path, task_timeout=600.0)
        rt = _make_running_task(fake_clock, elapsed_sec=700.0, annotations={"timeout_sec": "-10"})
        launcher._running[rt.uuid] = rt

        launcher.check_completions()

        rt.proc.terminate.assert_called_once()

    def test_sigkill_after_kill_grace_when_term_ignored(self, tmp_path, fake_clock) -> None:
        launcher = _make_launcher(tmp_path, task_timeout=600.0, kill_grace=5.0)
        rt = _make_running_task(
            fake_clock,
            elapsed_sec=700.0,
            annotations={"timeout_sec": "300"},
            term_sent_at=fake_clock.monotonic() - 6.0,
        )
        launcher._running[rt.uuid] = rt

        launcher.check_completions()

        rt.proc.terminate.assert_not_called()
        rt.proc.kill.assert_called_once()
