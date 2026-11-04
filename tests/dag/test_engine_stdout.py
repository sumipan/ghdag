"""Tests for DagEngine stdout direct write (JSONL result_path tasks)."""

from pathlib import Path
from unittest.mock import MagicMock

from ghdag_dag_conftest_helpers import (
    MemoryHooks,
    _jsonl_task,
    _make_jsonl_config,
    _run_engine_with_timeout,
)

from ghdag.dag.engine import DagEngine


class TestStdoutDirectWrite:
    """AC3, AC5-AC8: JSONL task with result_path — stdout capture and write"""

    def test_stdout_written_to_result_path_ac3(self, tmp_path):
        """With result_path set, stdout is written directly (AC3)"""
        result_path = str(tmp_path / "result.md")
        config = _make_jsonl_config(tmp_path, [
            _jsonl_task("uuid-a", "echo 'hello world'", result_path)
        ])
        hooks = MemoryHooks()
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=5.0)

        assert Path(result_path).exists()
        content = Path(result_path).read_text()
        assert "hello world" in content
        assert len(hooks.successes) == 1

    def test_pipeline_status_merge_done_calls_success_ac5(self, tmp_path):
        """stdout PIPELINE_STATUS: MERGE_DONE → on_task_success is called (AC5)"""
        result_path = str(tmp_path / "result.md")
        config = _make_jsonl_config(tmp_path, [
            _jsonl_task("uuid-a", "echo 'PIPELINE_STATUS: MERGE_DONE'", result_path)
        ])
        hooks = MemoryHooks()
        hooks.pipeline_status = "MERGE_DONE"
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=5.0)

        assert len(hooks.successes) == 1
        assert not hooks.failures

    def test_pipeline_status_impl_failed_calls_failure_ac6(self, tmp_path):
        """stdout PIPELINE_STATUS: IMPL_FAILED → on_task_failure is called (AC6)"""
        result_path = str(tmp_path / "result.md")
        config = _make_jsonl_config(tmp_path, [
            _jsonl_task("uuid-a", "echo 'PIPELINE_STATUS: IMPL_FAILED'", result_path)
        ])
        hooks = MemoryHooks()
        hooks.pipeline_status = "IMPL_FAILED"
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=5.0)

        assert len(hooks.failures) == 1
        assert "PIPELINE_FAILED:IMPL_FAILED" in hooks.failures[0][3]

    def test_rejected_calls_on_task_rejected_ac7(self, tmp_path):
        """stdout starting with REJECTED: → on_task_rejected is called (AC7)"""
        result_path = str(tmp_path / "result.md")
        config = _make_jsonl_config(tmp_path, [
            _jsonl_task("uuid-a", "echo 'REJECTED: reason'", result_path)
        ])
        hooks = MemoryHooks()
        hooks.rejected = True
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=5.0)

        assert len(hooks.rejected_events) == 1
        assert not hooks.successes

    def test_empty_stdout_calls_on_task_empty_result_ac8(self, tmp_path):
        """Empty stdout → on_task_empty_result is called (AC8)"""
        result_path = str(tmp_path / "result.md")
        config = _make_jsonl_config(tmp_path, [
            _jsonl_task("uuid-a", "true", result_path)
        ])
        hooks = MemoryHooks()
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=5.0)

        assert Path(result_path).exists()
        assert Path(result_path).stat().st_size == 0
        assert len(hooks.empty_results) == 1


def test_get_task_returns_loaded_task_or_none(tmp_path):
    config = _make_jsonl_config(tmp_path, [
        _jsonl_task("uuid-a", "echo hi", str(tmp_path / "result.md"))
    ])
    hooks = MagicMock()
    hooks.check_rejected.return_value = False
    hooks.check_pipeline_status.return_value = None
    engine = DagEngine(config, hooks)
    assert engine.get_task("uuid-a") is None

    _run_engine_with_timeout(engine, timeout=5.0)

    task = engine.get_task("uuid-a")
    assert task is not None
    assert task.uuid == "uuid-a"
    assert engine.get_task("uuid-unknown") is None
