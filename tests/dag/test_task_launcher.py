"""Tests for ghdag.dag.engine — §5.4 acceptance criteria."""

import time
from unittest.mock import MagicMock

from ghdag_dag_conftest_helpers import (
    MemoryHooks,
    _make_config,
)


class TestTaskLauncherUnit:
    """TaskLauncher unit tests: launch() starts a process, check_completions() detects done."""

    def test_launch_registers_running_task(self, tmp_path):

        from ghdag.dag.circuit_breaker import CircuitBreakerPolicy
        from ghdag.dag.hooks import DagHooks
        from ghdag.dag.models import Task
        from ghdag.dag.task_launcher import TaskLauncher

        config = _make_config(tmp_path, "")
        hooks = MagicMock(spec=DagHooks)
        hooks.check_rejected.return_value = False
        hooks.check_pipeline_status.return_value = None
        cb = CircuitBreakerPolicy(float("inf"), 2**31)
        fm = MagicMock()
        launcher = TaskLauncher(config, hooks, cb, fm, lambda _: None)

        task = Task(uuid="launch-test", command="true")
        launcher.launch("launch-test", task)

        assert launcher.is_running("launch-test")
        assert launcher.running_count == 1

        # wait for completion
        launcher._running["launch-test"].proc.wait()
        launcher.check_completions()
        assert not launcher.is_running("launch-test")
        assert launcher.running_count == 0

    def test_check_completions_detects_completed_process(self, tmp_path):
        import io
        import subprocess

        from ghdag.dag.circuit_breaker import CircuitBreakerPolicy
        from ghdag.dag.models import RunningTask, Task
        from ghdag.dag.task_launcher import TaskLauncher

        config = _make_config(tmp_path, "")
        hooks = MemoryHooks()
        cb = CircuitBreakerPolicy(float("inf"), 2**31)
        fm = MagicMock()
        fm.spawn = MagicMock()
        launcher = TaskLauncher(config, hooks, cb, fm, lambda _: None)

        proc = subprocess.Popen(["true"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        proc.wait()
        task = Task(uuid="done-test", command="true")
        rt = RunningTask(
            uuid="done-test", task=task, proc=proc,
            started_at=time.time(), started_at_mono=time.monotonic(),
            stderr_buf=io.BytesIO(b""), retry_depth=0,
        )
        launcher._running["done-test"] = rt

        launcher.check_completions()

        assert len(hooks.successes) == 1
        assert not launcher.is_running("done-test")
