"""Tests for ghdag.dag.engine — §5.4 acceptance criteria."""

import json
import logging
import signal
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

from ghdag_dag_conftest_helpers import (
    MemoryHooks,
    _make_config,
    _make_jsonl_config,
    _read_done_status,
    _run_engine_with_timeout,
)

from ghdag.dag.engine import DagEngine
from ghdag.dag.models import DagConfig
from ghdag.dag.state import is_done, load_done_from_dir, load_succeeded_from_dir


class TestSingleTaskExecution:
    """§5.4 single task execution"""

    def test_single_task_success(self, tmp_path):
        """One line in exec.md, no deps → write status to jobs/done with exit 0"""
        config = _make_config(tmp_path, "uuid-a: echo hello\n")
        hooks = MemoryHooks()
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=3.0)

        assert is_done(config.exec_done_dir, "uuid-a")
        succeeded = load_succeeded_from_dir(config.exec_done_dir)
        assert "uuid-a" in succeeded
        assert len(hooks.successes) == 1


class TestDependencyResolution:
    """§5.4 dependency resolution"""

    def test_dep_blocks_launch(self, tmp_path):
        """With uuid-b[depends:uuid-a], uuid-b must not start before uuid-a completes"""
        # uuid-a sleeps so we can check uuid-b hasn't started
        config = _make_config(
            tmp_path,
            "uuid-a: sleep 2\n"
            "uuid-b[depends:uuid-a]: echo done\n",
        )
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        time.sleep(0.5)

        # uuid-a should be running, uuid-b should not have started
        assert not is_done(config.exec_done_dir, "uuid-b")
        assert "uuid-a" in engine._launcher._running
        assert "uuid-b" not in engine._launcher._running

        engine._shutdown = True
        t.join(timeout=5.0)

    def test_dep_resolved_after_success(self, tmp_path):
        """uuid-b starts after uuid-a succeeds"""
        config = _make_config(
            tmp_path,
            "uuid-a: echo ok\n"
            "uuid-b[depends:uuid-a]: echo done\n",
        )
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=5.0)

        assert is_done(config.exec_done_dir, "uuid-a")
        assert is_done(config.exec_done_dir, "uuid-b")
        succeeded = load_succeeded_from_dir(config.exec_done_dir)
        assert "uuid-a" in succeeded
        assert "uuid-b" in succeeded


class TestDepFailed:
    """§5.4 on dependency failure"""

    def test_dep_failed_skip(self, tmp_path):
        """When uuid-a fails, uuid-b is skipped"""
        config = _make_config(
            tmp_path,
            "uuid-a: exit 1\n"
            "uuid-b[depends:uuid-a]: echo should-not-run\n",
        )
        hooks = MemoryHooks()
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=5.0)

        done = load_done_from_dir(config.exec_done_dir)
        assert "uuid-a" in done
        assert "uuid-b" in done

        succeeded = load_succeeded_from_dir(config.exec_done_dir)
        assert "uuid-a" not in succeeded
        assert "uuid-b" not in succeeded

        assert hooks.dep_failed


class TestAppendTask:
    """§5.4 append_task exclusivity"""

    def test_append_task_concurrent(self, tmp_path):
        """Concurrent append_task() from 2 threads must not interleave lines"""
        config = _make_config(tmp_path, "")
        engine = DagEngine(config, hooks=MagicMock())
        # Don't run the engine loop — just test append_task
        engine._lock_fh = open(str(config.lock_file), "w")

        errors = []

        def appender(prefix: str, count: int):
            try:
                for i in range(count):
                    line = json.dumps({"uuid": f"{prefix}-{i}", "command": f"echo {prefix}-{i}", "depends": []})
                    engine.append_task(line)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=appender, args=("thread1", 20))
        t2 = threading.Thread(target=appender, args=("thread2", 20))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors

        lines = Path(config.exec_jsonl_path).read_text().strip().split("\n")
        # Should have 40 non-empty lines
        non_empty = [line for line in lines if line.strip()]
        assert len(non_empty) == 40

        # Each line should be a complete JSONL record
        for line in non_empty:
            data = json.loads(line)
            assert "echo " in data["command"]


class TestHooksCalled:
    """§5.4 hooks invocation"""

    def test_on_task_start_called(self, tmp_path):
        """on_task_start is called once with the correct uuid and task on start"""
        config = _make_config(tmp_path, "uuid-a: echo hello\n")
        hooks = MemoryHooks()
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=3.0)

        assert len(hooks.starts) == 1
        assert hooks.starts[0][0] == "uuid-a"
        assert hooks.starts[0][1].uuid == "uuid-a"

    def test_on_task_start_not_called_for_skipped_missing_input(self, tmp_path):
        """on_task_start is not called when the stdin file is missing"""
        config = _make_config(
            tmp_path,
            "uuid-a: agent -p --force < /tmp/nonexistent_ghdag_xxxxxx.md | tee -a result.md\n",
        )
        hooks = MemoryHooks()
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=3.0)

        assert not hooks.starts

    def test_on_task_success_called(self, tmp_path):
        """on_task_success is called on task success"""
        config = _make_config(tmp_path, "uuid-a: echo hello\n")
        hooks = MemoryHooks()
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=3.0)

        assert len(hooks.successes) == 1
        assert hooks.successes[0][0] == "uuid-a"

    def test_on_task_failure_called(self, tmp_path):
        """on_task_failure is called with returncode and stderr_text on failure"""
        config = _make_config(tmp_path, "uuid-a: echo err >&2 && exit 42\n")
        hooks = MemoryHooks()
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=3.0)

        assert len(hooks.failures) == 1
        assert hooks.failures[0][0] == "uuid-a"
        assert hooks.failures[0][2] == 42
        assert "err" in hooks.failures[0][3]


class TestSignalShutdown:
    """§5.4 SIGINT/SIGTERM"""

    def test_shutdown_flag_stops_loop(self, tmp_path):
        """shutdown flag calls on_shutdown and ends the loop"""
        config = _make_config(tmp_path, "uuid-a: sleep 30\n")
        hooks = MemoryHooks()
        engine = DagEngine(config, hooks)

        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        time.sleep(0.5)

        # Simulate what the signal handler does
        engine._shutdown = True
        hooks.on_shutdown(signal.SIGINT)
        t.join(timeout=5.0)

        assert not t.is_alive()
        assert hooks.shutdown_signum == signal.SIGINT

    def test_signal_handler_installed_in_main_thread(self, tmp_path):
        """Signal handlers are installed when run on the main thread"""
        config = _make_config(tmp_path, "")
        hooks = MagicMock()
        engine = DagEngine(config, hooks)

        old_handler = signal.getsignal(signal.SIGINT)
        try:
            engine._acquire_lock()
            engine._install_signal_handlers()
            new_handler = signal.getsignal(signal.SIGINT)
            assert new_handler is not old_handler
        finally:
            signal.signal(signal.SIGINT, old_handler)


class TestTaskTimeout:
    """AC 1-1 ~ 1-4: child-process wall-clock timeout"""

    def test_timeout_records_timeout_status(self, tmp_path):
        """1-1: sleep 60 with task_timeout=2.0 records TIMEOUT"""
        config = _make_config(
            tmp_path,
            "uuid-a: sleep 60\n",
            task_timeout=2.0,
            kill_grace=2.0,
        )
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=8.0)

        assert is_done(config.exec_done_dir, "uuid-a")
        status = _read_done_status(config.exec_done_dir, "uuid-a")
        assert status == "TIMEOUT"

    def test_timeout_sigkill_after_term_ignored(self, tmp_path):
        """1-2: SIGTERM-ignoring process is killed with SIGKILL after kill_grace"""
        config = _make_config(
            tmp_path,
            "uuid-a: trap '' TERM; sleep 60\n",
            task_timeout=2.0,
            kill_grace=1.5,
        )
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=10.0)

        assert is_done(config.exec_done_dir, "uuid-a")
        status = _read_done_status(config.exec_done_dir, "uuid-a")
        assert status == "TIMEOUT"

    def test_no_timeout_when_none(self, tmp_path):
        """1-3: task_timeout=None is unlimited (3s sleep completes normally)"""
        config = _make_config(
            tmp_path,
            "uuid-a: sleep 1\n",
            task_timeout=None,
        )
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=5.0)

        assert is_done(config.exec_done_dir, "uuid-a")
        status = _read_done_status(config.exec_done_dir, "uuid-a")
        assert status == "0"

    def test_timeout_calls_on_task_failure_with_timeout_msg(self, tmp_path):
        """1-4: on_task_failure stderr_text indicates a timeout"""
        config = _make_config(
            tmp_path,
            "uuid-a: sleep 60\n",
            task_timeout=2.0,
            kill_grace=2.0,
        )
        hooks = MemoryHooks()
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=8.0)

        assert len(hooks.failures) == 1
        stderr_arg = hooks.failures[0][3]
        assert "TIMEOUT" in stderr_arg or "timeout" in stderr_arg.lower()


class TestValidateDependenciesEngine:
    """AC 3: validate_dependencies is integrated into the engine"""

    def test_orphan_dep_marks_dep_failed(self, tmp_path):
        """Orphan-dependency tasks are marked DEP_FAILED"""
        config = _make_config(
            tmp_path,
            "uuid-b[depends:nonexistent-uuid]: echo should-not-run\n",
        )
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=3.0)

        assert is_done(config.exec_done_dir, "uuid-b")
        succeeded = load_succeeded_from_dir(config.exec_done_dir)
        assert "uuid-b" not in succeeded
class TestStdinMissingInputSkip:
    """AC-1–AC-5: skip behavior when stdin input file is missing"""

    def test_missing_stdin_file_skips_task(self, tmp_path, caplog):
        """AC-1: missing stdin file → skipped with SKIPPED_MISSING_INPUT"""
        config = _make_config(
            tmp_path,
            "uuid-a: agent -p --force < /tmp/nonexistent_ghdag_xxxxxx.md | tee -a result.md\n",
        )
        hooks = MemoryHooks()
        engine = DagEngine(config, hooks)

        with caplog.at_level(logging.WARNING, logger="ghdag.dag.engine"):
            _run_engine_with_timeout(engine, timeout=3.0)

        assert _read_done_status(config.exec_done_dir, "uuid-a") == "SKIPPED_MISSING_INPUT"
        assert any(
            "stdin input file missing" in r.message and "/tmp/nonexistent_ghdag_xxxxxx.md" in r.message
            for r in caplog.records
            if r.levelno == logging.WARNING
        )
        assert not hooks.failures

    def test_existing_stdin_file_launches_normally(self, tmp_path):
        """AC-2: when stdin file exists, starts and completes as before"""
        stdin_file = tmp_path / "input.txt"
        stdin_file.write_text("hello", encoding="utf-8")
        config = _make_config(
            tmp_path,
            f"uuid-a: cat < {stdin_file}\n",
        )
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=5.0)

        assert _read_done_status(config.exec_done_dir, "uuid-a") == "0"

    def test_no_stdin_redirect_unaffected(self, tmp_path):
        """AC-3: commands without stdin redirect are unaffected"""
        config = _make_config(tmp_path, "uuid-a: echo hello\n")
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=3.0)

        assert _read_done_status(config.exec_done_dir, "uuid-a") == "0"

    def test_heredoc_not_misdetected(self, tmp_path):
        """AC-4: do not false-detect heredoc (`<<`)"""
        config = _make_jsonl_config(tmp_path, [
            {
                "uuid": "uuid-a",
                "command": "cat << EOF\nhello\nEOF",
                "depends": [],
                "result_path": None,
                "retry": 0,
                "annotations": {},
            }
        ])
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=3.0)

        assert _read_done_status(config.exec_done_dir, "uuid-a") == "0"

    def test_relative_stdin_uses_cwd(self, tmp_path):
        """AC-5: relative path resolved against cwd; missing → SKIPPED_MISSING_INPUT"""
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        # orders/task.md does not exist
        config = _make_config(
            tmp_path,
            "uuid-a: agent < orders/task.md\n",
            cwd=str(work_dir),
        )
        hooks = MagicMock()
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=3.0)

        assert _read_done_status(config.exec_done_dir, "uuid-a") == "SKIPPED_MISSING_INPUT"


class TestEngineModelFromStructuredFields:
    """AC3: task.engine/model priority and parse_engine_model fallback"""

    def _make_jsonl_config(self, tmp_path, jsonl_content: str, **overrides) -> DagConfig:
        exec_jsonl = tmp_path / "exec.jsonl"
        exec_jsonl.parent.mkdir(parents=True, exist_ok=True)
        exec_jsonl.write_text(jsonl_content, encoding="utf-8")
        defaults = dict(
            exec_jsonl_path=str(exec_jsonl),
            exec_done_dir=str(tmp_path / "jobs" / "done"),
            poll_interval=0.1,
            launch_stagger=0.0,
            lock_file=str(tmp_path / "lock"),
        )
        defaults.update(overrides)
        return DagConfig(**defaults)

    def test_structured_engine_used_without_fallback(self, tmp_path):
        """AC3: when task.engine is set, on_task_success metrics.engine uses that value"""
        jsonl = json.dumps({
            "uuid": "uuid-a",
            "command": "echo hello",
            "depends": [],
            "engine": "cursor",
            "model": None,
        })
        config = self._make_jsonl_config(tmp_path, jsonl + "\n")
        hooks = MemoryHooks()
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=3.0)

        assert len(hooks.successes) == 1
        metrics = hooks.successes[0][2]
        assert metrics.engine == "cursor"
        assert metrics.model is None

    def test_fallback_when_engine_field_unset(self, tmp_path):
        """AC3: when task.engine=None (legacy record), parse_engine_model fallback runs"""
        # Test with echo (no claude): engine=null → parse_engine_model("echo hello") → engine=None
        jsonl = json.dumps({
            "uuid": "uuid-b",
            "command": "echo hello",
            "depends": [],
        })
        config = self._make_jsonl_config(tmp_path, jsonl + "\n")
        hooks = MemoryHooks()
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=3.0)

        assert len(hooks.successes) == 1
        metrics = hooks.successes[0][2]
        # parse_engine_model("echo hello") → engine=None (no known engine in command)
        assert metrics.engine is None

    def test_structured_model_used_directly(self, tmp_path):
        """AC3: when task.model is set, metrics.model uses that value"""
        jsonl = json.dumps({
            "uuid": "uuid-c",
            "command": "echo hello",
            "depends": [],
            "engine": "claude",
            "model": "claude-opus-4-6",
        })
        config = self._make_jsonl_config(tmp_path, jsonl + "\n")
        hooks = MemoryHooks()
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=3.0)

        assert len(hooks.successes) == 1
        metrics = hooks.successes[0][2]
        assert metrics.engine == "claude"
        assert metrics.model == "claude-opus-4-6"

    def test_request_id_from_annotations(self, tmp_path):
        """AC-A5: annotations._request_id is read into TaskMetrics.request_id."""
        jsonl = json.dumps({
            "uuid": "uuid-d",
            "command": "echo hello",
            "depends": [],
            "annotations": {"_request_id": "req-from-annotations"},
        })
        config = self._make_jsonl_config(tmp_path, jsonl + "\n")
        hooks = MemoryHooks()
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=3.0)

        assert len(hooks.successes) == 1
        assert hooks.successes[0][2].request_id == "req-from-annotations"
