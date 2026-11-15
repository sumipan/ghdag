"""Tests for DagEngine stdout direct write (JSONL result_path tasks)."""

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
