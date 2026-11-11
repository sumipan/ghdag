"""Tests for ghdag.cli — AC1 - AC11.

Test approach:
- Parse validation via main(argv=[...])
- Mock DagEngine / WorkflowDispatcher; assert correct args
- Error cases assert SystemExit code
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ghdag.dag.models import DagConfig as RealDagConfig


def _capture_config(**kwargs):
    """Build a real DagConfig while recording kwargs for CLI wiring tests."""
    _capture_config.last_kwargs = kwargs  # type: ignore[attr-defined]
    return RealDagConfig(**kwargs)


_capture_config.last_kwargs = {}


class _RecordingEngine:
    """Minimal stand-in that records hooks and whether run() was invoked."""

    last_hooks = None
    ran = False

    def __init__(self, config, hooks):
        _RecordingEngine.last_hooks = hooks
        self.config = config

    def run(self):
        _RecordingEngine.ran = True


class TestRunNormal:
    def test_run_calls_dag_engine_with_defaults(self, tmp_path):
        exec_md = tmp_path / "exec.jsonl"
        exec_md.write_text("")

        _RecordingEngine.ran = False
        with patch("ghdag.dag.engine.DagEngine", _RecordingEngine), \
             patch("ghdag.dag.models.DagConfig", _capture_config):
            from ghdag.cli import main
            main(["run", str(exec_md)])

        kwargs = _capture_config.last_kwargs
        assert kwargs["exec_jsonl_path"] == str(exec_md)
        assert kwargs["poll_interval"] == 1.0
        assert "cwd" in kwargs
        from ghdag.dag.audit_hooks import AuditHooks
        assert isinstance(_RecordingEngine.last_hooks, AuditHooks)
        assert _RecordingEngine.ran is True

    def test_run_with_custom_interval(self, tmp_path):
        exec_md = tmp_path / "exec.jsonl"
        exec_md.write_text("")

        with patch("ghdag.dag.engine.DagEngine", _RecordingEngine), \
             patch("ghdag.dag.models.DagConfig", _capture_config):
            from ghdag.cli import main
            main(["run", str(exec_md), "--interval", "10"])

        kwargs = _capture_config.last_kwargs
        assert kwargs["exec_jsonl_path"] == str(exec_md)
        assert kwargs["poll_interval"] == 10.0
        assert "cwd" in kwargs


# ---------------------------------------------------------------------------
# AC3b: ghdag run --hooks
# ---------------------------------------------------------------------------


class TestRunHooks:
    def _make_hooks_module_with_class(self):
        """Return a module namespace with HOOKS_CLASS and a hooks class."""
        import types

        class MyHooks:
            def __init__(self): self.engine_set = False
            def set_engine(self, engine): self.engine_set = True
            def on_task_success(self, uuid, task): pass
            def on_task_failure(self, uuid, task, returncode, stderr_text): pass
            def on_task_rejected(self, uuid, task, retry_depth, is_final): pass
            def on_task_dep_failed(self, uuid, task, failed_dep): pass
            def on_task_empty_result(self, uuid, task, stderr_text): pass
            def on_shutdown(self, signum): pass
            def check_rejected(self, result_path): return False

        mod = types.ModuleType("my_hooks")
        mod.HOOKS_CLASS = MyHooks
        return mod, MyHooks

    def test_hooks_module_loaded_and_passed_to_engine(self, tmp_path):
        exec_md = tmp_path / "exec.jsonl"
        exec_md.write_text("")
        mock_module, MyHooks = self._make_hooks_module_with_class()

        with patch("ghdag.dag.engine.DagEngine", _RecordingEngine), \
             patch("ghdag.dag.models.DagConfig", _capture_config), \
             patch("importlib.import_module", return_value=mock_module):
            from ghdag.cli import main
            main(["run", str(exec_md), "--hooks", "my_hooks"])

        assert isinstance(_RecordingEngine.last_hooks, MyHooks)

    def test_hooks_set_engine_called(self, tmp_path):
        exec_md = tmp_path / "exec.jsonl"
        exec_md.write_text("")
        mock_module, MyHooks = self._make_hooks_module_with_class()

        _RecordingEngine.ran = False
        with patch("ghdag.dag.engine.DagEngine", _RecordingEngine), \
             patch("ghdag.dag.models.DagConfig", _capture_config), \
             patch("importlib.import_module", return_value=mock_module):
            from ghdag.cli import main
            main(["run", str(exec_md), "--hooks", "my_hooks"])

        assert _RecordingEngine.ran is True

    def test_hooks_invalid_module_exits_1(self, tmp_path, capsys):
        exec_md = tmp_path / "exec.jsonl"
        exec_md.write_text("")

        with pytest.raises(SystemExit) as exc:
            from ghdag.cli import main
            main(["run", str(exec_md), "--hooks", "nonexistent.module.path"])
        assert exc.value.code == 1
        assert "cannot import" in capsys.readouterr().err

    def test_no_hooks_arg_engine_called_with_audit_hooks(self, tmp_path):
        exec_md = tmp_path / "exec.jsonl"
        exec_md.write_text("")

        with patch("ghdag.dag.engine.DagEngine", _RecordingEngine), \
             patch("ghdag.dag.models.DagConfig", _capture_config):
            from ghdag.cli import main
            main(["run", str(exec_md)])

        from ghdag.dag.audit_hooks import AuditHooks
        assert isinstance(_RecordingEngine.last_hooks, AuditHooks)


# ---------------------------------------------------------------------------
# AC4: ghdag run error cases
# ---------------------------------------------------------------------------


class TestRunError:
    def test_run_no_args_exits_2(self):
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["run"])
        assert exc.value.code == 2

    def test_run_nonexistent_file_exits_1(self, capsys):
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["run", "nonexistent_exec.jsonl"])
        assert exc.value.code == 1
        assert "not found" in capsys.readouterr().err


class TestRunMaxConcurrency:
    def test_max_concurrency_arg(self, tmp_path):
        """--max-concurrency is passed correctly to DagConfig"""
        exec_md = tmp_path / "exec.jsonl"
        exec_md.write_text("")

        with patch("ghdag.dag.engine.DagEngine", _RecordingEngine), \
             patch("ghdag.dag.models.DagConfig", _capture_config):
            from ghdag.cli import main
            main(["run", str(exec_md), "--max-concurrency", "4"])

        assert _capture_config.last_kwargs["max_concurrency"] == 4

    def test_max_concurrency_default_none(self, tmp_path):
        """--max-concurrency omitted → None"""
        exec_md = tmp_path / "exec.jsonl"
        exec_md.write_text("")

        with patch("ghdag.dag.engine.DagEngine", _RecordingEngine), \
             patch("ghdag.dag.models.DagConfig", _capture_config):
            from ghdag.cli import main
            main(["run", str(exec_md)])

        assert _capture_config.last_kwargs["max_concurrency"] is None

    def test_max_concurrency_invalid_type_exits_2(self, tmp_path, capsys):
        """--max-concurrency abc exits via argparse error"""
        exec_md = tmp_path / "exec.md"
        exec_md.write_text("")

        from ghdag.cli import main
        with pytest.raises(SystemExit) as exc:
            main(["run", str(exec_md), "--max-concurrency", "abc"])
        assert exc.value.code == 2
