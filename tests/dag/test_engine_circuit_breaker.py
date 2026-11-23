"""Tests for CircuitBreakerPolicy and TaskLauncher integration."""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

from ghdag_dag_conftest_helpers import RUNNING_MONO_START, RUNNING_WALL_START

from ghdag.dag.circuit_breaker import CircuitBreakerPolicy
from ghdag.dag.engine import DagEngine
from ghdag.dag.hooks import DagHooks
from ghdag.dag.models import DagConfig, Task, _RunningProcess


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
    return _RunningProcess(
        uuid=uuid,
        task=task,
        proc=proc,
        started_at=RUNNING_WALL_START,
        started_at_mono=RUNNING_MONO_START,
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


    def test_failure_outside_window_resets_counter(self, fake_clock):
        cb = CircuitBreakerPolicy(failure_window_sec=0.0, max_consecutive_failures=2)
        cb.record_failure()
        fake_clock.advance(1.0)
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

    def test_record_failure_returns_false_after_already_tripped(self):
        cb = CircuitBreakerPolicy(failure_window_sec=float("inf"), max_consecutive_failures=1)
        assert cb.record_failure() is True
        assert cb.record_failure() is False


# --- TaskLauncher.check_completions circuit breaker integration tests ---

class TestTaskLauncherCircuitBreakerIntegration:


    @patch("ghdag.dag.task_launcher.state_mark_done")
    @patch("ghdag.dag.task_launcher._extract_tee_target", return_value=None)
    def test_timeout_increments_circuit_breaker(self, mock_tee, mock_mark_done, tmp_path, fake_clock):
        engine, hooks = _make_engine(tmp_path)
        rt = _make_running_task(returncode=-15)
        fake_clock.advance(1.0)
        rt.term_sent_at = fake_clock.monotonic() - 1.0
        engine._launcher._running[rt.uuid] = rt

        engine._launcher.check_completions()

        assert engine._circuit_breaker._consecutive_failures == 1


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
    def test_single_circuit_breaker_trip_audit_per_completion_pass(
        self, mock_tee, mock_mark_done, tmp_path
    ):
        """Refs sumipan/nexus#6936 AC5."""
        config = DagConfig(
            exec_jsonl_path=str(tmp_path / "exec.jsonl"),
            exec_done_dir=str(tmp_path / "done"),
            max_consecutive_failures=1,
        )
        hooks = MagicMock(spec=DagHooks)
        hooks.check_rejected.return_value = False
        hooks.check_pipeline_status.return_value = None
        engine = DagEngine(config, hooks)

        for suffix in ("a", "b"):
            uuid = f"cb-{suffix}"
            rt = _make_running_task(uuid=uuid, returncode=1)
            engine._launcher._running[uuid] = rt

        engine._launcher.check_completions()

        audit_path = tmp_path / "audit.jsonl"
        events = [
            json.loads(line)
            for line in audit_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        tripped = [e for e in events if e.get("event_type") == "circuit_breaker_tripped"]
        assert len(tripped) == 1
        assert tripped[0]["consecutive_failures"] == 1
