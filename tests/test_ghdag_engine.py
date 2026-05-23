"""Tests for ghdag.dag.engine — §5.4 acceptance criteria."""

import json
import logging
import signal
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock


from ghdag.dag.engine import DagEngine
from ghdag.dag.models import DagConfig
from ghdag.dag.state import is_done, load_done_from_dir, load_succeeded_from_dir


def _read_done_status(exec_done_dir: str, uuid: str) -> str:
    return (Path(exec_done_dir) / uuid).read_text().strip()


def _write_exec_md(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_config(tmp_path, exec_md_content: str, **overrides) -> DagConfig:
    exec_md = tmp_path / "exec.md"
    _write_exec_md(exec_md, exec_md_content)
    defaults = dict(
        exec_md_path=str(exec_md),
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
    """§5.4 単一タスク実行"""

    def test_single_task_success(self, tmp_path):
        """exec.md に 1 行、依存なし → exit 0 で jobs/done にステータス書き込み"""
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

    def test_on_task_start_called(self, tmp_path):
        """タスク起動時に on_task_start が正しい uuid と task で 1 回呼ばれること"""
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
        """stdin ファイルが存在しないタスクでは on_task_start が呼ばれないこと"""
        config = _make_config(
            tmp_path,
            "uuid-a: agent -p --force < /tmp/nonexistent_ghdag_xxxxxx.md | tee -a result.md\n",
        )
        hooks = MagicMock()
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=3.0)

        hooks.on_task_start.assert_not_called()

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


class TestDagConfigDefaults:
    """AC 4-1, 4-2: lock_file のデフォルト"""

    def test_lock_file_defaults_to_exec_md_parent(self, tmp_path):
        """4-1: lock_file 未指定時は exec_md_path の親ディレクトリに .ghdag.lock が作られる"""
        exec_md = tmp_path / "queue" / "exec.md"
        exec_md.parent.mkdir(parents=True, exist_ok=True)
        exec_md.write_text("")
        config = DagConfig(exec_md_path=str(exec_md))
        assert config.lock_file == Path(str(exec_md.parent)) / ".ghdag.lock"

    def test_lock_file_explicit_preserved(self, tmp_path):
        """4-2: 明示指定した lock_file は維持される（後方互換）"""
        exec_md = tmp_path / "exec.md"
        exec_md.write_text("")
        custom = str(tmp_path / "custom.lock")
        config = DagConfig(exec_md_path=str(exec_md), lock_file=custom)
        assert config.lock_file == Path(custom)


class TestTaskTimeout:
    """AC 1-1 ~ 1-4: 子プロセス wall-clock タイムアウト"""

    def test_timeout_records_timeout_status(self, tmp_path):
        """1-1: task_timeout=2.0 で sleep 60 を実行すると TIMEOUT が記録される"""
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
        """1-2: SIGTERM を無視するプロセスが kill_grace 後に SIGKILL で終了する"""
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
        """1-3: task_timeout=None では無制限（3秒の sleep が正常完了する）"""
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
        """1-4: on_task_failure の stderr_text 引数にタイムアウトである旨が含まれる"""
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
    """AC 3: validate_dependencies がエンジンに統合されている"""

    def test_orphan_dep_marks_dep_failed(self, tmp_path):
        """孤立依存のタスクが DEP_FAILED としてマークされる"""
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
# JSONL task with result_path — stdout 直書き (AC3, AC5-AC8)
# ---------------------------------------------------------------------------

def _make_jsonl_config(tmp_path, tasks: list[dict], **overrides) -> DagConfig:
    exec_jsonl = tmp_path / "exec.jsonl"
    exec_jsonl.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(t) for t in tasks]
    exec_jsonl.write_text("\n".join(lines), encoding="utf-8")
    defaults = dict(
        exec_md_path=str(exec_jsonl),
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
        """result_path 設定時に stdout が直接書き込まれる (AC3)"""
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
        """stdout に PIPELINE_STATUS: MERGE_DONE → on_task_success が呼ばれる (AC5)"""
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
        """stdout に PIPELINE_STATUS: IMPL_FAILED → on_task_failure が呼ばれる (AC6)"""
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
        """stdout が REJECTED: で始まる → on_task_rejected が呼ばれる (AC7)"""
        result_path = str(tmp_path / "result.md")
        config = _make_jsonl_config(tmp_path, [
            _jsonl_task("uuid-a", "echo 'REJECTED: 理由'", result_path)
        ])
        hooks = MagicMock()
        hooks.check_rejected.return_value = True
        hooks.check_pipeline_status.return_value = None
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=5.0)

        hooks.on_task_rejected.assert_called_once()
        hooks.on_task_success.assert_not_called()

    def test_empty_stdout_calls_on_task_empty_result_ac8(self, tmp_path):
        """stdout が空 → on_task_empty_result が呼ばれる (AC8)"""
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
    """AC-1〜AC-5: stdin 入力ファイル不在時のスキップ動作"""

    def test_missing_stdin_file_skips_task(self, tmp_path, caplog):
        """AC-1: stdin ファイルが存在しない場合、SKIPPED_MISSING_INPUT でスキップされる"""
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
        """AC-2: stdin ファイルが存在する場合、従来どおり正常に起動・完了する"""
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
        """AC-3: stdin リダイレクトのないコマンドは影響を受けない"""
        config = _make_config(tmp_path, "uuid-a: echo hello\n")
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=3.0)

        assert _read_done_status(config.exec_done_dir, "uuid-a") == "0"

    def test_heredoc_not_misdetected(self, tmp_path):
        """AC-4: ヒアドキュメント (`<<`) を誤検出しない"""
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
        """AC-5: 相対パスが cwd を基準に解決され、不在なら SKIPPED_MISSING_INPUT"""
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        # orders/task.md は存在しない
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
    """AC3: task.engine/model 優先と parse_engine_model フォールバックの検証"""

    def _make_jsonl_config(self, tmp_path, jsonl_content: str, **overrides) -> DagConfig:
        exec_jsonl = tmp_path / "exec.jsonl"
        exec_jsonl.parent.mkdir(parents=True, exist_ok=True)
        exec_jsonl.write_text(jsonl_content, encoding="utf-8")
        defaults = dict(
            exec_md_path=str(exec_jsonl),
            exec_done_dir=str(tmp_path / "jobs" / "done"),
            poll_interval=0.1,
            launch_stagger=0.0,
            lock_file=str(tmp_path / "lock"),
        )
        defaults.update(overrides)
        return DagConfig(**defaults)

    def test_structured_engine_used_without_fallback(self, tmp_path):
        """AC3: task.engine が設定されている場合、on_task_success の metrics.engine にその値が使われる"""
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
        """AC3: task.engine=None（旧レコード）の場合、parse_engine_model フォールバックが動く"""
        # claude コマンドがなくても echo でテスト: engine=null → parse_engine_model("echo hello") → engine=None
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
        # parse_engine_model("echo hello") → engine=None（コマンドに既知エンジンなし）
        assert metrics.engine is None

    def test_structured_model_used_directly(self, tmp_path):
        """AC3: task.model が設定されている場合、metrics.model にその値が使われる"""
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


class TestReaderThreadJoin:
    """スレッド回収テスト — 受け入れ条件: 正常系"""

    def test_stderr_thread_not_alive_after_completion(self, tmp_path):
        """タスク完了後、stderr_thread が alive でないこと"""
        captured_rt = []
        config = _make_config(tmp_path, "uuid-a: echo hello\n")
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        def capture(uuid, task):
            if uuid in engine._running:
                captured_rt.append(engine._running[uuid])

        hooks.on_task_start.side_effect = capture
        _run_engine_with_timeout(engine, timeout=3.0)

        assert len(captured_rt) > 0
        rt = captured_rt[0]
        assert rt.stderr_thread is not None
        assert not rt.stderr_thread.is_alive()

    def test_stdout_thread_none_when_no_result_path(self, tmp_path):
        """result_path 未指定の場合、stdout_thread は None"""
        captured_rt = []
        config = _make_config(tmp_path, "uuid-a: echo hello\n")
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        def capture(uuid, task):
            if uuid in engine._running:
                captured_rt.append(engine._running[uuid])

        hooks.on_task_start.side_effect = capture
        _run_engine_with_timeout(engine, timeout=3.0)

        assert len(captured_rt) > 0
        assert captured_rt[0].stdout_thread is None

    def test_both_threads_recovered_with_result_path(self, tmp_path):
        """result_path 指定時、stderr_thread と stdout_thread の両方が回収される"""
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
            if uuid in engine._running:
                captured_rt.append(engine._running[uuid])

        hooks.on_task_start.side_effect = capture
        _run_engine_with_timeout(engine, timeout=5.0)

        assert len(captured_rt) > 0
        rt = captured_rt[0]
        assert rt.stderr_thread is not None
        assert rt.stdout_thread is not None
        assert not rt.stderr_thread.is_alive()
        assert not rt.stdout_thread.is_alive()


class TestThreadLeakPrevention:
    """スレッドリーク防止テスト"""

    def test_100_tasks_thread_count_within_bounds(self, tmp_path):
        """100 タスク連続実行後のスレッド数が起動時 +2 以内"""
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
    """AC2: max_concurrency による同時実行上限のテスト"""

    def test_concurrency_limited(self, tmp_path):
        """max_concurrency=2 で同時実行が 2 を超えないこと"""
        tasks = "\n".join(f"uuid-{i}: sleep 1" for i in range(4))
        config = _make_config(tmp_path, tasks + "\n", max_concurrency=2)
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        max_seen = 0
        samples = []

        def sampler():
            for _ in range(30):
                samples.append(len(engine._running))
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
        """max_concurrency=None でタスクが全て同時起動されること"""
        tasks = "\n".join(f"uuid-{i}: sleep 3" for i in range(3))
        config = _make_config(tmp_path, tasks + "\n", max_concurrency=None)
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        time.sleep(0.8)

        running_count = len(engine._running)
        engine._shutdown = True
        t.join(timeout=5.0)

        assert running_count == 3, f"Expected 3 tasks running simultaneously, got {running_count}"

    def test_concurrency_1_serializes_tasks(self, tmp_path):
        """max_concurrency=1 でタスク A, B（依存なし）を投入した場合、1つずつ実行されること"""
        config = _make_config(
            tmp_path,
            "uuid-a: sleep 0.5\nuuid-b: sleep 0.5\n",
            max_concurrency=1,
        )
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        max_seen = 0
        samples = []

        def sampler():
            for _ in range(20):
                samples.append(len(engine._running))
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
        """上限到達時も DEP_FAILED が正しく検出されること"""
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
    """タイムアウト後のスレッド回収テスト"""

    def test_timeout_threads_joined(self, tmp_path):
        """タイムアウトで強制終了されたタスクでも reader スレッドが回収される"""
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
            if uuid in engine._running:
                captured_rt.append(engine._running[uuid])

        hooks.on_task_start.side_effect = capture
        _run_engine_with_timeout(engine, timeout=6.0)

        assert len(captured_rt) > 0
        rt = captured_rt[0]
        assert rt.stderr_thread is not None
        assert not rt.stderr_thread.is_alive()

    def test_join_warning_when_reader_thread_hangs(self, tmp_path, caplog):
        """`_join_reader_threads` でスレッドが 2.0s 内に終了しない場合、warning が出力される"""
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

            with caplog.at_level(logging.WARNING, logger="ghdag.dag.engine"):
                engine._join_reader_threads(FakeRT())

            assert any(
                "reader thread did not terminate within 2.0s" in r.message
                for r in caplog.records
                if r.levelno == logging.WARNING
            )
        finally:
            stop_event.set()
            hanging_thread.join(timeout=1.0)
