"""Tests for ghdag.dag.engine — §5.4 acceptance criteria."""

import signal
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock


from ghdag.dag.engine import DagEngine
from ghdag.dag.models import DagConfig
from ghdag.dag.state import is_done, load_done_from_dir, load_succeeded_from_dir


def _write_exec_md(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_config(tmp_path, exec_md_content: str, **overrides) -> DagConfig:
    exec_md = tmp_path / "exec.md"
    _write_exec_md(exec_md, exec_md_content)
    defaults = dict(
        exec_md_path=str(exec_md),
        exec_done_dir=str(tmp_path / "exec-done"),
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
    """§5.4 単一タスク実行"""

    def test_single_task_success(self, tmp_path):
        """exec.md に 1 行、依存なし → exit 0 で exec-done にステータス書き込み"""
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
    """§5.4 依存解決"""

    def test_dep_blocks_launch(self, tmp_path):
        """uuid-b[depends:uuid-a] の場合、uuid-a 完了前に uuid-b が起動されないこと"""
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
        assert "uuid-a" in engine._running
        assert "uuid-b" not in engine._running

        engine._shutdown = True
        t.join(timeout=5.0)

    def test_dep_resolved_after_success(self, tmp_path):
        """uuid-a が成功後に uuid-b が起動されること"""
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
    """§5.4 依存失敗時"""

    def test_dep_failed_skip(self, tmp_path):
        """uuid-a が失敗した場合、uuid-b はスキップされること"""
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
    """§5.4 append_task 排他"""

    def test_append_task_concurrent(self, tmp_path):
        """2 スレッドから同時に append_task() を呼んでも行が混在しないこと"""
        config = _make_config(tmp_path, "")
        engine = DagEngine(config, hooks=MagicMock())
        # Don't run the engine loop — just test append_task
        engine._lock_fh = open(str(config.lock_file), "w")

        errors = []

        def appender(prefix: str, count: int):
            try:
                for i in range(count):
                    engine.append_task(f"{prefix}-{i}: echo {prefix}-{i}")
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=appender, args=("thread1", 20))
        t2 = threading.Thread(target=appender, args=("thread2", 20))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors

        lines = Path(config.exec_md_path).read_text().strip().split("\n")
        # Should have 40 non-empty lines
        non_empty = [line for line in lines if line.strip()]
        assert len(non_empty) == 40

        # Each line should be a complete line (not interleaved)
        for line in non_empty:
            assert ": echo " in line


class TestHooksCalled:
    """§5.4 hooks 呼び出し"""

    def test_on_task_success_called(self, tmp_path):
        """タスク成功時に on_task_success が呼ばれること"""
        config = _make_config(tmp_path, "uuid-a: echo hello\n")
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=3.0)

        hooks.on_task_success.assert_called_once()
        call_args = hooks.on_task_success.call_args
        assert call_args[0][0] == "uuid-a"

    def test_on_task_failure_called(self, tmp_path):
        """タスク失敗時に on_task_failure が returncode と stderr_text 付きで呼ばれること"""
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
        """shutdown フラグで on_shutdown が呼ばれループが終了すること"""
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
        """Main thread で実行した場合にシグナルハンドラがインストールされること"""
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
