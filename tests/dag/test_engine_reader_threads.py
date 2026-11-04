"""Tests for ghdag.dag.engine — §5.4 acceptance criteria."""

import logging
import threading
import time
from unittest.mock import MagicMock

from ghdag_dag_conftest_helpers import (
    MemoryHooks,
    _jsonl_task,
    _make_config,
    _make_jsonl_config,
    _run_engine_with_timeout,
)

from ghdag.dag.engine import DagEngine


class TestReaderThreadJoin:
    """Thread join tests — acceptance: happy path"""

    def test_stderr_thread_not_alive_after_completion(self, tmp_path):
        """After task completion, stderr_thread is not alive"""
        captured_rt = []
        config = _make_config(tmp_path, "uuid-a: echo hello\n")
        hooks = MemoryHooks()
        engine = DagEngine(config, hooks)

        def capture(uuid, task):
            if uuid in engine._launcher._running:
                captured_rt.append(engine._launcher._running[uuid])

        hooks._on_task_start_extra = capture
        _run_engine_with_timeout(engine, timeout=3.0)

        assert len(captured_rt) > 0
        rt = captured_rt[0]
        assert rt.stderr_thread is not None
        assert not rt.stderr_thread.is_alive()

    def test_stdout_thread_none_when_no_result_path(self, tmp_path):
        """Without result_path, stdout_thread is None"""
        captured_rt = []
        config = _make_config(tmp_path, "uuid-a: echo hello\n")
        hooks = MemoryHooks()
        engine = DagEngine(config, hooks)

        def capture(uuid, task):
            if uuid in engine._launcher._running:
                captured_rt.append(engine._launcher._running[uuid])

        hooks._on_task_start_extra = capture
        _run_engine_with_timeout(engine, timeout=3.0)

        assert len(captured_rt) > 0
        assert captured_rt[0].stdout_thread is None

    def test_both_threads_recovered_with_result_path(self, tmp_path):
        """With result_path, both stderr_thread and stdout_thread are joined"""
        captured_rt = []
        result_path = str(tmp_path / "result.md")
        config = _make_jsonl_config(tmp_path, [
            _jsonl_task("uuid-a", "echo hello", result_path)
        ])
        hooks = MemoryHooks()
        engine = DagEngine(config, hooks)

        def capture(uuid, task):
            if uuid in engine._launcher._running:
                captured_rt.append(engine._launcher._running[uuid])

        hooks._on_task_start_extra = capture
        _run_engine_with_timeout(engine, timeout=5.0)

        assert len(captured_rt) > 0
        rt = captured_rt[0]
        assert rt.stderr_thread is not None
        assert rt.stdout_thread is not None
        assert not rt.stderr_thread.is_alive()
        assert not rt.stdout_thread.is_alive()


class TestThreadLeakPrevention:
    """Thread-leak prevention tests"""

    def test_100_tasks_thread_count_within_bounds(self, tmp_path):
        """After 100 sequential tasks, thread count is within start + 2"""
        baseline = threading.active_count()
        tasks = "\n".join(f"uuid-{i:03d}: echo task{i}" for i in range(100))
        config = _make_config(tmp_path, tasks + "\n")
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=20.0)
        time.sleep(0.5)

        final_count = threading.active_count()
        assert final_count <= baseline + 2, (
            f"Thread leak detected: baseline={baseline}, final={final_count}"
        )


class TestTimeoutReaderJoin:
    """Thread join after timeout"""

    def test_timeout_threads_joined(self, tmp_path):
        """Reader threads are joined even for tasks force-killed by timeout"""
        captured_rt = []
        config = _make_config(
            tmp_path,
            "uuid-a: sleep 60\n",
            task_timeout=1.0,
            kill_grace=1.0,
        )
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        def capture(uuid, task):
            if uuid in engine._launcher._running:
                captured_rt.append(engine._launcher._running[uuid])

        hooks.on_task_start.side_effect = capture
        _run_engine_with_timeout(engine, timeout=6.0)

        assert len(captured_rt) > 0
        rt = captured_rt[0]
        assert rt.stderr_thread is not None
        assert not rt.stderr_thread.is_alive()

    def test_join_warning_when_reader_thread_hangs(self, tmp_path, caplog):
        """`_join_reader_threads` emits a warning if threads do not finish within 2.0s"""
        stop_event = threading.Event()

        def hanging_reader():
            stop_event.wait(timeout=30.0)

        hanging_thread = threading.Thread(target=hanging_reader, daemon=True)
        hanging_thread.start()
        try:
            config = _make_config(tmp_path, "")
            engine = DagEngine(config, MagicMock())

            class FakeRT:
                uuid = "test-uuid"
                stderr_thread = hanging_thread
                stdout_thread = None

            with caplog.at_level(logging.WARNING, logger="ghdag.dag.task_launcher"):
                engine._launcher._join_reader_threads(FakeRT())

            assert any(
                "reader thread did not terminate within 2.0s" in r.message
                for r in caplog.records
                if r.levelno == logging.WARNING
            )
        finally:
            stop_event.set()
            hanging_thread.join(timeout=1.0)
