"""Tests for PIPELINE_STATUS parser and engine integration — Issue #477."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

from ghdag.dag._util import check_pipeline_status
from ghdag.dag.engine import DagEngine
from ghdag.dag.models import DagConfig
from ghdag.dag.state import load_done_from_dir, load_succeeded_from_dir


class TestCheckPipelineStatus:

    def test_done_status_returns_string(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text("PIPELINE_STATUS: IMPL_DONE\n", encoding="utf-8")
        assert check_pipeline_status(str(f)) == "IMPL_DONE"

    def test_no_pipeline_status_returns_none(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text("some output\nwithout status line\n", encoding="utf-8")
        assert check_pipeline_status(str(f)) is None

    def test_impl_failed_returns_string(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text("PIPELINE_STATUS: IMPL_FAILED\n", encoding="utf-8")
        assert check_pipeline_status(str(f)) == "IMPL_FAILED"

    def test_brushup_failed_returns_string(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text("PIPELINE_STATUS: BRUSHUP_FAILED\n", encoding="utf-8")
        assert check_pipeline_status(str(f)) == "BRUSHUP_FAILED"

    def test_last_match_wins(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text(
            "PIPELINE_STATUS: IMPL_DONE\nsome text\nPIPELINE_STATUS: IMPL_FAILED\n",
            encoding="utf-8",
        )
        assert check_pipeline_status(str(f)) == "IMPL_FAILED"

    def test_no_space_after_colon(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text("PIPELINE_STATUS:IMPL_FAILED\n", encoding="utf-8")
        assert check_pipeline_status(str(f)) == "IMPL_FAILED"

    def test_leading_space_not_detected(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text("  PIPELINE_STATUS: IMPL_FAILED\n", encoding="utf-8")
        assert check_pipeline_status(str(f)) is None

    def test_missing_file_returns_none(self, tmp_path):
        assert check_pipeline_status(str(tmp_path / "nonexistent.md")) is None

    def test_empty_value_not_matched(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text("PIPELINE_STATUS:   \n", encoding="utf-8")
        assert check_pipeline_status(str(f)) is None


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
    t = threading.Thread(target=engine.run, daemon=True)
    t.start()
    t.join(timeout=timeout)
    engine._shutdown = True
    t.join(timeout=2.0)


class TestEngineCheckPipelineStatus:

    def test_pipeline_failed_via_hook(self, tmp_path):
        result_file = tmp_path / "result.md"
        result_file.parent.mkdir(parents=True, exist_ok=True)
        cmd = f"echo PIPELINE_STATUS: IMPL_FAILED | tee {result_file}"
        config = _make_config(tmp_path, f"uuid-a: {cmd}\n")
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        hooks.check_pipeline_status.return_value = "IMPL_FAILED"
        engine = DagEngine(config, hooks)
        _run_engine_with_timeout(engine, timeout=5.0)
        done = load_done_from_dir(config.exec_done_dir)
        assert "uuid-a" in done
        succeeded = load_succeeded_from_dir(config.exec_done_dir)
        assert "uuid-a" not in succeeded

    def test_pipeline_done_is_success(self, tmp_path):
        result_file = tmp_path / "result.md"
        result_file.parent.mkdir(parents=True, exist_ok=True)
        cmd = f"echo PIPELINE_STATUS: IMPL_DONE | tee {result_file}"
        config = _make_config(tmp_path, f"uuid-a: {cmd}\n")
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        hooks.check_pipeline_status.return_value = "IMPL_DONE"
        engine = DagEngine(config, hooks)
        _run_engine_with_timeout(engine, timeout=5.0)
        succeeded = load_succeeded_from_dir(config.exec_done_dir)
        assert "uuid-a" in succeeded

    def test_exit_code_nonzero_skips_pipeline_status_check(self, tmp_path):
        config = _make_config(tmp_path, "uuid-a: exit 1\n")
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)
        _run_engine_with_timeout(engine, timeout=5.0)
        hooks.check_pipeline_status.assert_not_called()
        done = load_done_from_dir(config.exec_done_dir)
        assert "uuid-a" in done
        succeeded = load_succeeded_from_dir(config.exec_done_dir)
        assert "uuid-a" not in succeeded

    def test_dep_failed_propagation_after_pipeline_failed(self, tmp_path):
        result_file = tmp_path / "result.md"
        result_file.parent.mkdir(parents=True, exist_ok=True)
        cmd_a = f"echo PIPELINE_STATUS: IMPL_FAILED | tee {result_file}"
        config = _make_config(
            tmp_path,
            f"uuid-a: {cmd_a}\nuuid-b[depends:uuid-a]: echo should-not-run\n",
        )
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        hooks.check_pipeline_status.return_value = "IMPL_FAILED"
        engine = DagEngine(config, hooks)
        _run_engine_with_timeout(engine, timeout=5.0)
        done = load_done_from_dir(config.exec_done_dir)
        assert "uuid-a" in done
        assert "uuid-b" in done
        succeeded = load_succeeded_from_dir(config.exec_done_dir)
        assert "uuid-a" not in succeeded
        assert "uuid-b" not in succeeded
        hooks.on_task_dep_failed.assert_called()


class TestDefaultHooksCheckPipelineStatus:

    def test_default_hooks_returns_failed(self, tmp_path):
        from ghdag.dag.hooks import DefaultHooks
        f = tmp_path / "result.md"
        f.write_text("PIPELINE_STATUS: IMPL_FAILED\n", encoding="utf-8")
        dh = DefaultHooks()
        assert dh.check_pipeline_status(str(f)) == "IMPL_FAILED"

    def test_default_hooks_returns_none(self, tmp_path):
        from ghdag.dag.hooks import DefaultHooks
        f = tmp_path / "result.md"
        f.write_text("no status here\n", encoding="utf-8")
        dh = DefaultHooks()
        assert dh.check_pipeline_status(str(f)) is None
