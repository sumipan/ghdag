"""Tests for CircuitBreakerPolicy and TaskLauncher integration."""

from __future__ import annotations

import io
import time
from unittest.mock import MagicMock, patch

from ghdag.dag.circuit_breaker import CircuitBreakerPolicy
from ghdag.dag.engine import DagEngine
from ghdag.dag.hooks import DagHooks
from ghdag.dag.models import DagConfig, RunningTask, Task


def _make_config(tmp_path):
    return DagConfig(
        exec_jsonl_path=str(tmp_path / "exec.jsonl"),
        exec_done_dir=str(tmp_path / "done"),
    )


def _make_running_task(uuid="cb-uuid", returncode=1, stderr=b""):
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


# --- CircuitBreakerPolicy unit tests ---

class TestCircuitBreakerPolicy:
    def test_trips_after_max_consecutive_failures(self):
        cb = CircuitBreakerPolicy(failure_window_sec=float("inf"), max_consecutive_failures=3)
        assert not cb.tripped
        cb.record_failure()
        assert not cb.tripped
        cb.record_failure()
        assert not cb.tripped
        cb.record_failure()
        assert cb.tripped

    def test_reset_clears_counter(self):
        cb = CircuitBreakerPolicy(failure_window_sec=float("inf"), max_consecutive_failures=2)
        cb.record_failure()
        cb.reset()
        cb.record_failure()
        assert not cb.tripped

    def test_failure_outside_window_resets_counter(self):
        cb = CircuitBreakerPolicy(failure_window_sec=0.0, max_consecutive_failures=2)
        cb.record_failure()
        # Artificially age the last failure time past the window
        cb._last_failure_time = time.monotonic() - 1.0
        cb.record_failure()
        assert not cb.tripped

    def test_record_failure_returns_true_on_trip(self):
        cb = CircuitBreakerPolicy(failure_window_sec=float("inf"), max_consecutive_failures=1)
        tripped = cb.record_failure()
        assert tripped is True

    def test_record_failure_returns_false_before_trip(self):
        cb = CircuitBreakerPolicy(failure_window_sec=float("inf"), max_consecutive_failures=3)
        assert cb.record_failure() is False
        assert cb.record_failure() is False


# --- TaskLauncher.check_completions circuit breaker integration tests ---

class TestTaskLauncherCircuitBreakerIntegration:
    @patch("ghdag.dag.task_launcher.state_mark_done")
    @patch("ghdag.dag.task_launcher._extract_tee_target", return_value=None)
    def test_failure_increments_circuit_breaker(self, mock_tee, mock_mark_done, tmp_path):
        engine, hooks = _make_engine(tmp_path)
        rt = _make_running_task(returncode=1)
        engine._launcher._running[rt.uuid] = rt

        engine._launcher.check_completions()

        assert engine._circuit_breaker._consecutive_failures == 1

    @patch("ghdag.dag.task_launcher.state_mark_done")
    @patch("ghdag.dag.task_launcher._extract_tee_target", return_value=None)
    def test_success_resets_circuit_breaker(self, mock_tee, mock_mark_done, tmp_path):
        engine, hooks = _make_engine(tmp_path)
        engine._circuit_breaker._consecutive_failures = 2
        rt = _make_running_task(returncode=0)
        engine._launcher._running[rt.uuid] = rt

        engine._launcher.check_completions()

        assert engine._circuit_breaker._consecutive_failures == 0

    @patch("ghdag.dag.task_launcher.state_mark_done")
    @patch("ghdag.dag.task_launcher._extract_tee_target", return_value=None)
    def test_timeout_increments_circuit_breaker(self, mock_tee, mock_mark_done, tmp_path):
        engine, hooks = _make_engine(tmp_path)
        rt = _make_running_task(returncode=-15)
        rt.term_sent_at = time.monotonic() - 1.0
        engine._launcher._running[rt.uuid] = rt

        engine._launcher.check_completions()

        assert engine._circuit_breaker._consecutive_failures == 1

    @patch("ghdag.dag.task_launcher.state_mark_done")
    @patch("ghdag.dag.task_launcher._extract_tee_target", return_value="result.md")
    def test_pipeline_failed_increments_circuit_breaker(self, mock_tee, mock_mark_done, tmp_path):
        engine, hooks = _make_engine(tmp_path)
        hooks.check_rejected.return_value = False
        hooks.check_pipeline_status.return_value = "IMPL_FAILED"
        rt = _make_running_task(returncode=0)
        engine._launcher._running[rt.uuid] = rt

        engine._launcher.check_completions()

        assert engine._circuit_breaker._consecutive_failures == 1

    @patch("ghdag.dag.task_launcher.state_mark_done")
    @patch("ghdag.dag.task_launcher._extract_tee_target", return_value=None)
    def test_multiple_failures_trip_breaker(self, mock_tee, mock_mark_done, tmp_path):
        engine, hooks = _make_engine(tmp_path)
        engine._circuit_breaker._max_consecutive_failures = 3

        for i in range(3):
            rt = _make_running_task(uuid=f"fail-{i}", returncode=1)
            engine._launcher._running[rt.uuid] = rt

        engine._launcher.check_completions()

        assert engine._circuit_breaker.tripped

    @patch("ghdag.dag.task_launcher.state_mark_done")
    @patch("ghdag.dag.task_launcher._extract_tee_target", return_value=None)
    def test_rejected_does_not_increment_circuit_breaker(self, mock_tee, mock_mark_done, tmp_path):
        engine, hooks = _make_engine(tmp_path)
        hooks.check_rejected.return_value = True
        rt = _make_running_task(returncode=0)
        engine._launcher._running[rt.uuid] = rt

        engine._launcher.check_completions()

        assert engine._circuit_breaker._consecutive_failures == 0

    @patch("ghdag.dag.task_launcher.state_mark_done")
    def test_empty_result_does_not_increment_circuit_breaker(self, mock_mark_done, tmp_path):
        engine, hooks = _make_engine(tmp_path)
        result_file = tmp_path / "result.md"
        result_file.write_text("")
        rt = _make_running_task(returncode=0)
        engine._launcher._running[rt.uuid] = rt

        with patch("ghdag.dag.task_launcher._extract_tee_target", return_value=str(result_file)):
            engine._launcher.check_completions()

        assert engine._circuit_breaker._consecutive_failures == 0

    @patch("ghdag.dag.task_launcher.state_mark_done")
    @patch("ghdag.dag.task_launcher._extract_tee_target", return_value=None)
    def test_tripped_breaker_does_not_reset_on_further_failure(self, mock_tee, mock_mark_done, tmp_path):
        engine, hooks = _make_engine(tmp_path)
        engine._circuit_breaker._tripped = True

        rt = _make_running_task(returncode=0)
        engine._launcher._running[rt.uuid] = rt
        engine._launcher.check_completions()

        assert engine._circuit_breaker.tripped

    @patch("ghdag.dag.task_launcher.state_mark_done")
    @patch("ghdag.dag.task_launcher._extract_tee_target", return_value=None)
    def test_infinite_threshold_circuit_breaker_never_trips(self, mock_tee, mock_mark_done, tmp_path):
        engine, hooks = _make_engine(tmp_path)
        engine._circuit_breaker._max_consecutive_failures = 2**31

        for i in range(100):
            rt = _make_running_task(uuid=f"fail-{i}", returncode=1)
            engine._launcher._running[rt.uuid] = rt

        engine._launcher.check_completions()

        assert not engine._circuit_breaker.tripped

    @patch("ghdag.dag.task_launcher.state_mark_done")
    @patch("ghdag.dag.task_launcher._extract_tee_target", return_value=None)
    def test_failure_and_success_cycle_resets(self, mock_tee, mock_mark_done, tmp_path):
        engine, hooks = _make_engine(tmp_path)
        engine._circuit_breaker._max_consecutive_failures = 3

        for i in range(2):
            rt = _make_running_task(uuid=f"fail-{i}", returncode=1)
            engine._launcher._running[rt.uuid] = rt
        engine._launcher.check_completions()
        assert engine._circuit_breaker._consecutive_failures == 2

        rt_ok = _make_running_task(uuid="ok-task", returncode=0)
        engine._launcher._running[rt_ok.uuid] = rt_ok
        engine._launcher.check_completions()
        assert engine._circuit_breaker._consecutive_failures == 0
        assert not engine._circuit_breaker.tripped
