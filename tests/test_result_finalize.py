"""Tests for result_finalize policy (AC1-AC3 from issue #1140)."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock

from ghdag.dag.engine import DagEngine
from ghdag.dag.models import DagConfig
from ghdag.dag.parser import parse_jsonl

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(tmp_path: Path, records: list[dict], **overrides) -> DagConfig:
    exec_jsonl = tmp_path / "exec.jsonl"
    exec_jsonl.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
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


def _run_engine(engine: DagEngine, timeout: float = 5.0) -> None:
    t = threading.Thread(target=engine.run, daemon=True)
    t.start()
    t.join(timeout=timeout)
    engine._shutdown = True
    t.join(timeout=2.0)


def _make_hooks(*, rejected: bool = False) -> MagicMock:
    hooks = MagicMock()
    hooks.check_rejected.return_value = rejected
    hooks.check_pipeline_status.return_value = None
    hooks.check_promote_target.return_value = None
    return hooks


# ---------------------------------------------------------------------------
# AC1: result_finalize policy branch tests
# ---------------------------------------------------------------------------

class TestResultFinalizePolicy:
    """AC1: verify preserve_nonempty / stdout_only behavior"""

    def test_preserve_nonempty_keeps_existing_content(self, tmp_path):
        """preserve_nonempty + nonempty file → discard stdout and keep the file"""
        result_path = tmp_path / "result.md"
        long_content = "X" * 500
        result_path.write_text(long_content, encoding="utf-8")

        config = _make_config(tmp_path, [
            {
                "uuid": "uuid-a",
                "command": "echo 'Completed'",
                "depends": [],
                "result_path": str(result_path),
                "retry": 0,
                "annotations": {},
                "result_finalize": "preserve_nonempty",
            }
        ])
        hooks = _make_hooks()
        engine = DagEngine(config, hooks)
        _run_engine(engine, timeout=5.0)

        assert result_path.read_text(encoding="utf-8") == long_content

    def test_preserve_nonempty_empty_file_writes_stdout(self, tmp_path):
        """preserve_nonempty + empty file → write from stdout"""
        result_path = tmp_path / "result.md"
        result_path.write_text("", encoding="utf-8")

        config = _make_config(tmp_path, [
            {
                "uuid": "uuid-a",
                "command": "printf 'Analysis result...'",
                "depends": [],
                "result_path": str(result_path),
                "retry": 0,
                "annotations": {},
                "result_finalize": "preserve_nonempty",
            }
        ])
        hooks = _make_hooks()
        engine = DagEngine(config, hooks)
        _run_engine(engine, timeout=5.0)

        content = result_path.read_text(encoding="utf-8")
        assert "Analysis result..." in content

    def test_preserve_nonempty_missing_file_writes_stdout(self, tmp_path):
        """preserve_nonempty + missing file → create file from stdout"""
        result_path = tmp_path / "result.md"
        assert not result_path.exists()

        config = _make_config(tmp_path, [
            {
                "uuid": "uuid-a",
                "command": "printf 'Analysis result...'",
                "depends": [],
                "result_path": str(result_path),
                "retry": 0,
                "annotations": {},
                "result_finalize": "preserve_nonempty",
            }
        ])
        hooks = _make_hooks()
        engine = DagEngine(config, hooks)
        _run_engine(engine, timeout=5.0)

        assert result_path.exists()
        content = result_path.read_text(encoding="utf-8")
        assert "Analysis result..." in content

    def test_stdout_only_overwrites_existing_content(self, tmp_path):
        """stdout_only + nonempty file → overwrite from stdout"""
        result_path = tmp_path / "result.md"
        result_path.write_text("X" * 500, encoding="utf-8")

        config = _make_config(tmp_path, [
            {
                "uuid": "uuid-a",
                "command": "printf 'New result'",
                "depends": [],
                "result_path": str(result_path),
                "retry": 0,
                "annotations": {},
                "result_finalize": "stdout_only",
            }
        ])
        hooks = _make_hooks()
        engine = DagEngine(config, hooks)
        _run_engine(engine, timeout=5.0)

        content = result_path.read_text(encoding="utf-8")
        assert content == "New result"

    def test_result_finalize_none_defaults_to_preserve_nonempty(self, tmp_path):
        """result_finalize omitted (None) → same behavior as preserve_nonempty"""
        result_path = tmp_path / "result.md"
        long_content = "Y" * 500
        result_path.write_text(long_content, encoding="utf-8")

        config = _make_config(tmp_path, [
            {
                "uuid": "uuid-a",
                "command": "echo 'Done'",
                "depends": [],
                "result_path": str(result_path),
                "retry": 0,
                "annotations": {},
                # omit result_finalize
            }
        ])
        hooks = _make_hooks()
        engine = DagEngine(config, hooks)
        _run_engine(engine, timeout=5.0)

        assert result_path.read_text(encoding="utf-8") == long_content


# ---------------------------------------------------------------------------
# AC2: parser can parse result_finalize
# ---------------------------------------------------------------------------

class TestParserResultFinalize:
    """AC2: JSONL parser reads the result_finalize field"""

    def test_parse_stdout_only(self):
        """Can parse result_finalize: \"stdout_only\""""
        text = '{"uuid":"x","command":"echo hi","result_finalize":"stdout_only"}\n'
        tasks = parse_jsonl(text)
        assert len(tasks) == 1
        assert tasks[0].result_finalize == "stdout_only"

    def test_parse_preserve_nonempty(self):
        """Can parse result_finalize: \"preserve_nonempty\""""
        text = '{"uuid":"x","command":"echo hi","result_finalize":"preserve_nonempty"}\n'
        tasks = parse_jsonl(text)
        assert len(tasks) == 1
        assert tasks[0].result_finalize == "preserve_nonempty"

    def test_parse_missing_field_is_none(self):
        """No result_finalize key → task.result_finalize is None"""
        text = '{"uuid":"x","command":"echo hi"}\n'
        tasks = parse_jsonl(text)
        assert len(tasks) == 1
        assert tasks[0].result_finalize is None


# ---------------------------------------------------------------------------
# AC3: result_path deletion on retry
# ---------------------------------------------------------------------------

class TestRetryResultPathCleanup:
    """AC3: result_path is deleted before a rejected task is retried"""

    def test_retry_clears_result_path_before_callback(self, tmp_path):
        """On rejected (is_final=False), result_path is deleted"""
        result_path = tmp_path / "result.md"
        result_path.write_text("REJECTED: reason\nold content", encoding="utf-8")

        result_path_at_callback: list[bool] = []

        def on_rejected(uuid, task, retry_depth, is_final, metrics):
            result_path_at_callback.append(result_path.exists())

        config = _make_config(tmp_path, [
            {
                "uuid": "uuid-a",
                "command": "printf 'REJECTED: reason'",
                "depends": [],
                "result_path": str(result_path),
                "retry": 0,
                "annotations": {},
            }
        ], max_retry=1)

        hooks = _make_hooks(rejected=True)
        hooks.on_task_rejected.side_effect = on_rejected
        engine = DagEngine(config, hooks)
        _run_engine(engine, timeout=5.0)

        hooks.on_task_rejected.assert_called_once()
        _, kwargs_call = hooks.on_task_rejected.call_args
        args_call = hooks.on_task_rejected.call_args[0]
        # expect is_final=False (retry_depth=0 < max_retry=1)
        assert args_call[3] is False  # is_final
        # result_path should already be deleted at callback time
        assert result_path_at_callback == [False]

    def test_final_rejected_does_not_clear_result_path(self, tmp_path):
        """On REJECTED_FINAL, do not delete result_path"""
        result_path = tmp_path / "result.md"
        original_content = "REJECTED: final\nold content"
        result_path.write_text(original_content, encoding="utf-8")

        result_path_at_callback: list[bool] = []

        def on_rejected(uuid, task, retry_depth, is_final, metrics):
            result_path_at_callback.append(result_path.exists())

        config = _make_config(tmp_path, [
            {
                "uuid": "uuid-a",
                "command": "printf 'REJECTED: final'",
                "depends": [],
                "result_path": str(result_path),
                "retry": 1,  # already retried
                "annotations": {},
            }
        ], max_retry=1)

        hooks = _make_hooks(rejected=True)
        hooks.on_task_rejected.side_effect = on_rejected
        engine = DagEngine(config, hooks)
        _run_engine(engine, timeout=5.0)

        hooks.on_task_rejected.assert_called_once()
        # REJECTED_FINAL so the file should still exist
        assert result_path_at_callback == [True]
