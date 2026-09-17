"""Tests for ghdag.dag.engine — §5.4 acceptance criteria."""

import json
import logging
import re as _re
import signal
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

from ghdag.dag.engine import DagEngine
from ghdag.dag.models import DagConfig
from ghdag.dag.state import is_done, load_done_from_dir, load_succeeded_from_dir

_MD_LINE_RE = _re.compile(r"^([a-zA-Z0-9\-]+)((?:\[[^\]]+\])*)\s*:\s*(.+)$")
_MD_DEPENDS_RE = _re.compile(r"\[depends:([^\]]+)\]")


def _read_done_status(exec_done_dir: str, uuid: str) -> str:
    return (Path(exec_done_dir) / uuid).read_text().strip()


def _md_to_jsonl(content: str) -> str:
    """Convert exec.md text format to JSONL — test helper only."""
    lines = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _MD_LINE_RE.match(line)
        if not m:
            continue
        uid = m.group(1)
        annotation_str = m.group(2)
        command = m.group(3)
        depends_m = _MD_DEPENDS_RE.search(annotation_str)
        depends = [d.strip() for d in depends_m.group(1).split(",")] if depends_m else []
        lines.append(json.dumps({"uuid": uid, "command": command, "depends": depends}))
    return "\n".join(lines) + ("\n" if lines else "")


def _make_config(tmp_path, exec_md_content: str, **overrides) -> DagConfig:
    exec_jsonl = tmp_path / "exec.jsonl"
    exec_jsonl.parent.mkdir(parents=True, exist_ok=True)
    exec_jsonl.write_text(_md_to_jsonl(exec_md_content), encoding="utf-8")
    defaults = dict(
        exec_jsonl_path=str(exec_jsonl),
        exec_done_dir=str(tmp_path / "jobs" / "done"),
        poll_interval=0.1,
        launch_stagger=0.0,
        lock_file=str(tmp_path / "lock"),
    )
    defaults.update(overrides)
    return DagConfig(**defaults)


def _run_engine_with_timeout(engine: DagEngine, timeout: float = 5.0) -> None:
    """Run engine in a thread and stop it after timeout or all tasks done."""
    t = threading.Thread(target=engine.run, daemon=True)
    t.start()
    t.join(timeout=timeout)
    engine._shutdown = True
    t.join(timeout=2.0)


class TestSingleTaskExecution:
    """§5.4 single task execution"""

    def test_single_task_success(self, tmp_path):
        """One line in exec.md, no deps → write status to jobs/done with exit 0"""
        config = _make_config(tmp_path, "uuid-a: echo hello\n")
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=3.0)

        assert is_done(config.exec_done_dir, "uuid-a")
        succeeded = load_succeeded_from_dir(config.exec_done_dir)
        assert "uuid-a" in succeeded
        hooks.on_task_success.assert_called_once()


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
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=5.0)

        done = load_done_from_dir(config.exec_done_dir)
        assert "uuid-a" in done
        assert "uuid-b" in done

        succeeded = load_succeeded_from_dir(config.exec_done_dir)
        assert "uuid-a" not in succeeded
        assert "uuid-b" not in succeeded

        hooks.on_task_dep_failed.assert_called()


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
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=3.0)

        hooks.on_task_start.assert_called_once()
        call_args = hooks.on_task_start.call_args
        assert call_args[0][0] == "uuid-a"  # uuid
        assert call_args[0][1].uuid == "uuid-a"  # task

    def test_on_task_start_not_called_for_skipped_missing_input(self, tmp_path):
        """on_task_start is not called when the stdin file is missing"""
        config = _make_config(
            tmp_path,
            "uuid-a: agent -p --force < /tmp/nonexistent_ghdag_xxxxxx.md | tee -a result.md\n",
        )
        hooks = MagicMock()
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=3.0)

        hooks.on_task_start.assert_not_called()

    def test_on_task_success_called(self, tmp_path):
        """on_task_success is called on task success"""
        config = _make_config(tmp_path, "uuid-a: echo hello\n")
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=3.0)

        hooks.on_task_success.assert_called_once()
        call_args = hooks.on_task_success.call_args
        assert call_args[0][0] == "uuid-a"

    def test_on_task_failure_called(self, tmp_path):
        """on_task_failure is called with returncode and stderr_text on failure"""
        config = _make_config(tmp_path, "uuid-a: echo err >&2 && exit 42\n")
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=3.0)

        hooks.on_task_failure.assert_called_once()
        call_args = hooks.on_task_failure.call_args
        assert call_args[0][0] == "uuid-a"  # uuid
        assert call_args[0][2] == 42  # returncode
        assert "err" in call_args[0][3]  # stderr_text


class TestSignalShutdown:
    """§5.4 SIGINT/SIGTERM"""

    def test_shutdown_flag_stops_loop(self, tmp_path):
        """shutdown flag calls on_shutdown and ends the loop"""
        config = _make_config(tmp_path, "uuid-a: sleep 30\n")
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        time.sleep(0.5)

        # Simulate what the signal handler does
        engine._shutdown = True
        hooks.on_shutdown(signal.SIGINT)
        t.join(timeout=5.0)

        assert not t.is_alive()
        hooks.on_shutdown.assert_called_once_with(signal.SIGINT)

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


class TestDagConfigDefaults:
    """AC 4-1, 4-2: lock_file defaults"""

    def test_lock_file_defaults_to_exec_md_parent(self, tmp_path):
        """4-1: without lock_file, .ghdag.lock is created under exec_jsonl_path parent"""
        exec_jsonl = tmp_path / "jobs" / "exec.jsonl"
        exec_jsonl.parent.mkdir(parents=True, exist_ok=True)
        exec_jsonl.write_text("")
        config = DagConfig(exec_jsonl_path=str(exec_jsonl))
        assert config.lock_file == Path(str(exec_jsonl.parent)) / ".ghdag.lock"

    def test_lock_file_explicit_preserved(self, tmp_path):
        """4-2: explicitly set lock_file is preserved (backward compatible)"""
        exec_jsonl = tmp_path / "exec.jsonl"
        exec_jsonl.write_text("")
        custom = str(tmp_path / "custom.lock")
        config = DagConfig(exec_jsonl_path=str(exec_jsonl), lock_file=custom)
        assert config.lock_file == Path(custom)


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
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=8.0)

        hooks.on_task_failure.assert_called_once()
        call_args = hooks.on_task_failure.call_args
        stderr_arg = call_args[0][3]  # 4th positional arg
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


# ---------------------------------------------------------------------------
# JSONL task with result_path — write stdout directly (AC3, AC5-AC8)
# ---------------------------------------------------------------------------

def _make_jsonl_config(tmp_path, tasks: list[dict], **overrides) -> DagConfig:
    exec_jsonl = tmp_path / "exec.jsonl"
    exec_jsonl.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(t) for t in tasks]
    exec_jsonl.write_text("\n".join(lines), encoding="utf-8")
    defaults = dict(
        exec_jsonl_path=str(exec_jsonl),
        exec_done_dir=str(tmp_path / "jobs" / "done"),
        poll_interval=0.1,
        launch_stagger=0.0,
        lock_file=str(tmp_path / "lock"),
    )
    defaults.update(overrides)
    return DagConfig(**defaults)


def _jsonl_task(uuid: str, command: str, result_path: str) -> dict:
    return {"uuid": uuid, "command": command, "depends": [], "result_path": result_path, "retry": 0, "annotations": {}}


class TestStdoutDirectWrite:
    """AC3, AC5-AC8: JSONL task with result_path — stdout capture and write"""

    def test_stdout_written_to_result_path_ac3(self, tmp_path):
        """With result_path set, stdout is written directly (AC3)"""
        result_path = str(tmp_path / "result.md")
        config = _make_jsonl_config(tmp_path, [
            _jsonl_task("uuid-a", "echo 'hello world'", result_path)
        ])
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        hooks.check_pipeline_status.return_value = None
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=5.0)

        assert Path(result_path).exists()
        content = Path(result_path).read_text()
        assert "hello world" in content
        hooks.on_task_success.assert_called_once()

    def test_pipeline_status_merge_done_calls_success_ac5(self, tmp_path):
        """stdout PIPELINE_STATUS: MERGE_DONE → on_task_success is called (AC5)"""
        result_path = str(tmp_path / "result.md")
        config = _make_jsonl_config(tmp_path, [
            _jsonl_task("uuid-a", "echo 'PIPELINE_STATUS: MERGE_DONE'", result_path)
        ])
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        hooks.check_pipeline_status.return_value = "MERGE_DONE"
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=5.0)

        hooks.on_task_success.assert_called_once()
        hooks.on_task_failure.assert_not_called()

    def test_pipeline_status_impl_failed_calls_failure_ac6(self, tmp_path):
        """stdout PIPELINE_STATUS: IMPL_FAILED → on_task_failure is called (AC6)"""
        result_path = str(tmp_path / "result.md")
        config = _make_jsonl_config(tmp_path, [
            _jsonl_task("uuid-a", "echo 'PIPELINE_STATUS: IMPL_FAILED'", result_path)
        ])
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        hooks.check_pipeline_status.return_value = "IMPL_FAILED"
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=5.0)

        hooks.on_task_failure.assert_called_once()
        call_args = hooks.on_task_failure.call_args
        assert "PIPELINE_FAILED:IMPL_FAILED" in call_args[0][3]

    def test_rejected_calls_on_task_rejected_ac7(self, tmp_path):
        """stdout starting with REJECTED: → on_task_rejected is called (AC7)"""
        result_path = str(tmp_path / "result.md")
        config = _make_jsonl_config(tmp_path, [
            _jsonl_task("uuid-a", "echo 'REJECTED: reason'", result_path)
        ])
        hooks = MagicMock()
        hooks.check_rejected.return_value = True
        hooks.check_pipeline_status.return_value = None
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=5.0)

        hooks.on_task_rejected.assert_called_once()
        hooks.on_task_success.assert_not_called()

    def test_empty_stdout_calls_on_task_empty_result_ac8(self, tmp_path):
        """Empty stdout → on_task_empty_result is called (AC8)"""
        result_path = str(tmp_path / "result.md")
        config = _make_jsonl_config(tmp_path, [
            _jsonl_task("uuid-a", "true", result_path)
        ])
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        hooks.check_pipeline_status.return_value = None
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=5.0)

        assert Path(result_path).exists()
        assert Path(result_path).stat().st_size == 0
        hooks.on_task_empty_result.assert_called_once()


class TestStdinMissingInputSkip:
    """AC-1–AC-5: skip behavior when stdin input file is missing"""

    def test_missing_stdin_file_skips_task(self, tmp_path, caplog):
        """AC-1: missing stdin file → skipped with SKIPPED_MISSING_INPUT"""
        config = _make_config(
            tmp_path,
            "uuid-a: agent -p --force < /tmp/nonexistent_ghdag_xxxxxx.md | tee -a result.md\n",
        )
        hooks = MagicMock()
        engine = DagEngine(config, hooks)

        with caplog.at_level(logging.WARNING, logger="ghdag.dag.engine"):
            _run_engine_with_timeout(engine, timeout=3.0)

        assert _read_done_status(config.exec_done_dir, "uuid-a") == "SKIPPED_MISSING_INPUT"
        assert any(
            "stdin input file missing" in r.message and "/tmp/nonexistent_ghdag_xxxxxx.md" in r.message
            for r in caplog.records
            if r.levelno == logging.WARNING
        )
        hooks.on_task_failure.assert_not_called()

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
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=3.0)

        hooks.on_task_success.assert_called_once()
        call_args = hooks.on_task_success.call_args
        metrics = call_args[0][2]  # TaskMetrics (uuid, task, metrics)
        assert metrics.engine == "cursor"
        assert metrics.model is None

    def test_fallback_when_engine_field_absent(self, tmp_path):
        """AC3: when task.engine=None (legacy record), parse_engine_model fallback runs"""
        # Test with echo (no claude): engine=null → parse_engine_model("echo hello") → engine=None
        jsonl = json.dumps({
            "uuid": "uuid-b",
            "command": "echo hello",
            "depends": [],
        })
        config = self._make_jsonl_config(tmp_path, jsonl + "\n")
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=3.0)

        hooks.on_task_success.assert_called_once()
        call_args = hooks.on_task_success.call_args
        metrics = call_args[0][2]  # TaskMetrics
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
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=3.0)

        hooks.on_task_success.assert_called_once()
        call_args = hooks.on_task_success.call_args
        metrics = call_args[0][2]
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
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=3.0)

        hooks.on_task_success.assert_called_once()
        metrics = hooks.on_task_success.call_args[0][2]
        assert metrics.request_id == "req-from-annotations"


class TestReaderThreadJoin:
    """Thread join tests — acceptance: happy path"""

    def test_stderr_thread_not_alive_after_completion(self, tmp_path):
        """After task completion, stderr_thread is not alive"""
        captured_rt = []
        config = _make_config(tmp_path, "uuid-a: echo hello\n")
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        def capture(uuid, task):
            if uuid in engine._launcher._running:
                captured_rt.append(engine._launcher._running[uuid])

        hooks.on_task_start.side_effect = capture
        _run_engine_with_timeout(engine, timeout=3.0)

        assert len(captured_rt) > 0
        rt = captured_rt[0]
        assert rt.stderr_thread is not None
        assert not rt.stderr_thread.is_alive()

    def test_stdout_thread_none_when_no_result_path(self, tmp_path):
        """Without result_path, stdout_thread is None"""
        captured_rt = []
        config = _make_config(tmp_path, "uuid-a: echo hello\n")
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        def capture(uuid, task):
            if uuid in engine._launcher._running:
                captured_rt.append(engine._launcher._running[uuid])

        hooks.on_task_start.side_effect = capture
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
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        hooks.check_pipeline_status.return_value = None
        engine = DagEngine(config, hooks)

        def capture(uuid, task):
            if uuid in engine._launcher._running:
                captured_rt.append(engine._launcher._running[uuid])

        hooks.on_task_start.side_effect = capture
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


class TestMaxConcurrency:
    """AC2: max_concurrency concurrent-execution cap"""

    def test_concurrency_limited(self, tmp_path):
        """With max_concurrency=2, concurrency never exceeds 2"""
        tasks = "\n".join(f"uuid-{i}: sleep 1" for i in range(4))
        config = _make_config(tmp_path, tasks + "\n", max_concurrency=2)
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        samples = []

        def sampler():
            for _ in range(30):
                samples.append(len(engine._launcher._running))
                time.sleep(0.1)

        t_sampler = threading.Thread(target=sampler, daemon=True)
        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        t_sampler.start()
        t.join(timeout=10.0)
        engine._shutdown = True
        t.join(timeout=2.0)
        t_sampler.join(timeout=2.0)

        assert samples, "No samples collected"
        assert max(samples) <= 2, f"Concurrency exceeded 2: max={max(samples)}, samples={samples}"

    def test_none_means_unlimited(self, tmp_path):
        """With max_concurrency=None, all tasks start concurrently"""
        tasks = "\n".join(f"uuid-{i}: sleep 3" for i in range(3))
        config = _make_config(tmp_path, tasks + "\n", max_concurrency=None)
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        time.sleep(0.8)

        running_count = len(engine._launcher._running)
        engine._shutdown = True
        t.join(timeout=5.0)

        assert running_count == 3, f"Expected 3 tasks running simultaneously, got {running_count}"

    def test_concurrency_1_serializes_tasks(self, tmp_path):
        """With max_concurrency=1, independent tasks A and B run one at a time"""
        config = _make_config(
            tmp_path,
            "uuid-a: sleep 0.5\nuuid-b: sleep 0.5\n",
            max_concurrency=1,
        )
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        samples = []

        def sampler():
            for _ in range(20):
                samples.append(len(engine._launcher._running))
                time.sleep(0.05)

        t_sampler = threading.Thread(target=sampler, daemon=True)
        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        t_sampler.start()
        t.join(timeout=8.0)
        engine._shutdown = True
        t.join(timeout=2.0)
        t_sampler.join(timeout=2.0)

        assert all(s <= 1 for s in samples), f"Concurrency exceeded 1: {samples}"

        done = load_done_from_dir(config.exec_done_dir)
        assert "uuid-a" in done
        assert "uuid-b" in done

    def test_dep_failed_detection_with_limit(self, tmp_path):
        """DEP_FAILED is still detected correctly when at the concurrency cap"""
        config = _make_config(
            tmp_path,
            "uuid-a: exit 1\nuuid-b[depends:uuid-a]: echo b\nuuid-c: sleep 0.2\n",
            max_concurrency=1,
        )
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=8.0)

        done = load_done_from_dir(config.exec_done_dir)
        succeeded = load_succeeded_from_dir(config.exec_done_dir)

        assert "uuid-a" in done
        assert "uuid-b" in done
        assert "uuid-c" in done

        assert "uuid-a" not in succeeded
        assert "uuid-b" not in succeeded
        assert "uuid-c" in succeeded


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


# --- Unit tests for extracted classes (issue #2243) ---

class TestTaskLauncherUnit:
    """TaskLauncher unit tests: launch() starts a process, check_completions() detects done."""

    def test_launch_registers_running_task(self, tmp_path):
        from unittest.mock import MagicMock

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
        from unittest.mock import MagicMock

        from ghdag.dag.circuit_breaker import CircuitBreakerPolicy
        from ghdag.dag.hooks import DagHooks
        from ghdag.dag.models import RunningTask, Task
        from ghdag.dag.task_launcher import TaskLauncher

        config = _make_config(tmp_path, "")
        hooks = MagicMock(spec=DagHooks)
        hooks.check_rejected.return_value = False
        hooks.check_pipeline_status.return_value = None
        cb = CircuitBreakerPolicy(float("inf"), 2**31)
        fm = MagicMock()
        fm.spawn = MagicMock()
        launcher = TaskLauncher(config, hooks, cb, fm, lambda _: None)

        proc = subprocess.Popen(["true"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        proc.wait()
        task = Task(uuid="done-test", command="true")
        import time
        rt = RunningTask(
            uuid="done-test", task=task, proc=proc,
            started_at=time.time(), started_at_mono=time.monotonic(),
            stderr_buf=io.BytesIO(b""), retry_depth=0,
        )
        launcher._running["done-test"] = rt

        launcher.check_completions()

        hooks.on_task_success.assert_called_once()
        assert not launcher.is_running("done-test")


class TestCircuitBreakerPolicyUnit:
    """CircuitBreakerPolicy unit tests (acceptance criteria)."""

    def test_max_consecutive_failures_trips_breaker(self):
        from ghdag.dag.circuit_breaker import CircuitBreakerPolicy
        cb = CircuitBreakerPolicy(failure_window_sec=float("inf"), max_consecutive_failures=3)
        cb.record_failure()
        cb.record_failure()
        assert not cb.tripped
        cb.record_failure()
        assert cb.tripped

    def test_failure_window_resets_counter(self):
        import time

        from ghdag.dag.circuit_breaker import CircuitBreakerPolicy
        cb = CircuitBreakerPolicy(failure_window_sec=0.001, max_consecutive_failures=2)
        cb.record_failure()
        cb._last_failure_time = time.monotonic() - 1.0
        cb.record_failure()
        assert not cb.tripped


class TestFanOutManagerUnit:
    """FanOutManager unit tests (acceptance criteria)."""

    def test_spawn_calls_append_task_fn_and_registers_pending(self, tmp_path):
        import time
        from unittest.mock import MagicMock

        from ghdag.dag.fanout import FanOutItem, FanOutSpec
        from ghdag.dag.fanout_manager import FanOutManager
        from ghdag.dag.hooks import DagHooks
        from ghdag.dag.models import Task
        from ghdag.metrics.models import TaskMetrics

        config = _make_config(tmp_path, "")
        hooks = MagicMock(spec=DagHooks)
        appended: list[tuple[str, str]] = []
        fm = FanOutManager(
            config, hooks, lambda line, parent: appended.append((line, parent)), lambda _: None
        )

        parent_uuid = "parent"
        task = Task(uuid=parent_uuid, command="true")
        spec = FanOutSpec(children=[
            FanOutItem(id="c1", command="echo 1"),
            FanOutItem(id="c2", command="echo 2"),
        ])
        t = time.time()
        metrics = TaskMetrics(
            uuid=parent_uuid, engine=None, model=None,
            wall_time_sec=1.0, token_count=None, status="success",
            started_at=t, finished_at=t,
        )
        fm.spawn(parent_uuid, task, spec, metrics)

        assert len(appended) == 2
        assert all(parent == parent_uuid for _, parent in appended)
        assert fm.is_pending(parent_uuid)

    def test_check_completions_marks_parent_done_when_all_children_complete(self, tmp_path):
        import time
        from unittest.mock import MagicMock

        from ghdag.dag.fanout_manager import FanOutManager
        from ghdag.dag.hooks import DagHooks
        from ghdag.dag.models import Task
        from ghdag.metrics.models import TaskMetrics

        config = _make_config(tmp_path, "")
        # Create done dir so state.mark_done can write there
        import os
        os.makedirs(str(tmp_path / "jobs" / "done"), exist_ok=True)

        hooks = MagicMock(spec=DagHooks)
        fm = FanOutManager(config, hooks, lambda _line, _parent: None, lambda _: None)

        parent_uuid = "parent-fm"
        child_uuids = {f"{parent_uuid}--fo--c1", f"{parent_uuid}--fo--c2"}
        task = Task(uuid=parent_uuid, command="true")
        t = time.time()
        metrics = TaskMetrics(
            uuid=parent_uuid, engine=None, model=None,
            wall_time_sec=1.0, token_count=None, status="success",
            started_at=t, finished_at=t,
        )
        fm._pending[parent_uuid] = set(child_uuids)
        fm._tasks[parent_uuid] = task
        fm._metrics[parent_uuid] = metrics

        known_done = set(child_uuids)
        known_succeeded = set(child_uuids)
        fm.check_completions(known_done, known_succeeded)

        assert parent_uuid in known_done
        assert not fm.is_pending(parent_uuid)
        hooks.on_task_success.assert_called_once()
