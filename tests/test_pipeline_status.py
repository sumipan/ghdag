"""Tests for PIPELINE_STATUS parser and engine integration — Issue #477."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

from ghdag.dag import check_pipeline_status
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

    def test_reply_block_failed_code(self, tmp_path) -> None:
        f = tmp_path / "result.md"
        f.write_text("```ghdag\nstatus: failed\ncode: SCHEMA\n```\n", encoding="utf-8")
        assert check_pipeline_status(str(f)) == "SCHEMA_FAILED"

    def test_invalid_reply_block_returns_reply_block_failed(self, tmp_path) -> None:
        f = tmp_path / "result.md"
        f.write_text("```ghdag\n: not yaml\n```\n", encoding="utf-8")
        assert check_pipeline_status(str(f)) == "REPLY_BLOCK_FAILED"

    def test_reply_block_ok_ignores_legacy_pipeline_status_line(self, tmp_path) -> None:
        f = tmp_path / "result.md"
        f.write_text(
            "PIPELINE_STATUS: X_FAILED\n```ghdag\nstatus: ok\n```\n",
            encoding="utf-8",
        )
        assert check_pipeline_status(str(f)) is None

    def test_reply_block_wait_quota(self, tmp_path) -> None:
        f = tmp_path / "result.md"
        f.write_text(
            "```ghdag\nstatus: wait_quota\nengines: [claude]\n```\n",
            encoding="utf-8",
        )
        assert check_pipeline_status(str(f)) == "WAIT_QUOTA"


import json as _json


def _make_config(tmp_path, records: list[dict], **overrides) -> DagConfig:
    exec_jsonl = tmp_path / "exec.jsonl"
    exec_jsonl.parent.mkdir(parents=True, exist_ok=True)
    exec_jsonl.write_text(
        "\n".join(_json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )
    defaults = dict(
        exec_jsonl_path=str(exec_jsonl),
        exec_done_dir=str(tmp_path / "jobs" / "done"),
        poll_interval=0.1,
        launch_stagger=0.0,
        lock_file=str(tmp_path / "lock"),
    )
    defaults.update(overrides)
    return DagConfig(**defaults)


def _run_engine_with_timeout(
    engine: DagEngine,
    hooks: MagicMock,
    *,
    expected_completions: int = 1,
) -> None:
    idle = threading.Event()
    seen = {"count": 0}

    def _on_completion(*_args, **_kwargs) -> None:
        seen["count"] += 1
        if seen["count"] >= expected_completions:
            idle.set()

    hooks.on_task_success.side_effect = _on_completion
    hooks.on_task_failure.side_effect = _on_completion
    hooks.on_task_dep_failed.side_effect = _on_completion

    t = threading.Thread(target=engine.run, daemon=True)
    t.start()
    idle.wait()
    engine._shutdown = True
    t.join()


class TestEngineCheckPipelineStatus:

    def test_pipeline_failed_via_hook(self, tmp_path):
        result_file = tmp_path / "result.md"
        result_file.parent.mkdir(parents=True, exist_ok=True)
        cmd = f"echo PIPELINE_STATUS: IMPL_FAILED | tee {result_file}"
        config = _make_config(tmp_path, [{"uuid": "uuid-a", "command": cmd, "depends": []}])
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        hooks.check_pipeline_status.return_value = "IMPL_FAILED"
        engine = DagEngine(config, hooks)
        _run_engine_with_timeout(engine, hooks)
        done = load_done_from_dir(config.exec_done_dir)
        assert "uuid-a" in done
        succeeded = load_succeeded_from_dir(config.exec_done_dir)
        assert "uuid-a" not in succeeded

    def test_pipeline_done_is_success(self, tmp_path):
        result_file = tmp_path / "result.md"
        result_file.parent.mkdir(parents=True, exist_ok=True)
        cmd = f"echo PIPELINE_STATUS: IMPL_DONE | tee {result_file}"
        config = _make_config(tmp_path, [{"uuid": "uuid-a", "command": cmd, "depends": []}])
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        hooks.check_pipeline_status.return_value = "IMPL_DONE"
        engine = DagEngine(config, hooks)
        _run_engine_with_timeout(engine, hooks)
        succeeded = load_succeeded_from_dir(config.exec_done_dir)
        assert "uuid-a" in succeeded

    def test_exit_code_nonzero_skips_pipeline_status_check(self, tmp_path):
        config = _make_config(tmp_path, [{"uuid": "uuid-a", "command": "exit 1", "depends": []}])
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)
        _run_engine_with_timeout(engine, hooks)
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
            [
                {"uuid": "uuid-a", "command": cmd_a, "depends": []},
                {"uuid": "uuid-b", "command": "echo should-not-run", "depends": ["uuid-a"]},
            ],
        )
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        hooks.check_pipeline_status.return_value = "IMPL_FAILED"
        engine = DagEngine(config, hooks)
        _run_engine_with_timeout(engine, hooks, expected_completions=2)
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
