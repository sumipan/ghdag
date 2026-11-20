"""Tests for ghdag.cli — AC1 - AC11.

Test approach:
- Parse validation via main(argv=[...])
- Mock DagEngine / WorkflowDispatcher; assert correct args
- Error cases assert SystemExit code
"""

from __future__ import annotations

from pathlib import Path
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
    last_config = None
    ran = False

    def __init__(self, config, hooks):
        _RecordingEngine.last_hooks = hooks
        _RecordingEngine.last_config = config
        self.config = config

    def run(self):
        _RecordingEngine.ran = True


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

    def test_hooks_invalid_module_exits_1(self, tmp_path, capsys):
        exec_md = tmp_path / "exec.jsonl"
        exec_md.write_text("")

        with pytest.raises(SystemExit) as exc:
            from ghdag.cli import main
            main(["run", str(exec_md), "--hooks", "nonexistent.module.path"])
        assert exc.value.code == 1
        assert "cannot import" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# AC4: ghdag run error cases
# ---------------------------------------------------------------------------


class TestRunError:
    def test_run_nonexistent_file_exits_1(self, capsys):
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["run", "nonexistent_exec.jsonl"])
        assert exc.value.code == 1
        assert "not found" in capsys.readouterr().err

    def test_run_nonexistent_file_json_error(self, capsys):
        """Refs sumipan/ghdag#7551 AC3."""
        import json

        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["run", "nonexistent_exec.jsonl", "--json"])
        assert exc.value.code == 1
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["error"]["code"] == "EXEC_JSONL_NOT_FOUND"
        assert payload["error"]["message"] == "file not found: nonexistent_exec.jsonl"


# ---------------------------------------------------------------------------
# Issue sumipan/nexus#7095: run CLI task timeout, quota path, multi-hooks
# ---------------------------------------------------------------------------


class TestRunCliOptions:
    def test_run_task_timeout_passed_to_config(self, tmp_path):
        """Refs sumipan/nexus#7095 AC1."""
        exec_md = tmp_path / "exec.jsonl"
        exec_md.write_text("")

        with patch("ghdag.dag.engine.DagEngine", _RecordingEngine), \
             patch("ghdag.dag.models.DagConfig", _capture_config):
            from ghdag.cli import main
            main(["run", str(exec_md), "--task-timeout", "30"])

        assert _capture_config.last_kwargs["task_timeout"] == 30.0

    def test_run_quota_state_path_passed_to_config(self, tmp_path):
        """Refs sumipan/nexus#7095 AC2."""
        exec_md = tmp_path / "exec.jsonl"
        exec_md.write_text("")
        quota = tmp_path / "q.json"

        with patch("ghdag.dag.engine.DagEngine", _RecordingEngine), \
             patch("ghdag.dag.models.DagConfig", _capture_config):
            from ghdag.cli import main
            main(["run", str(exec_md), "--quota-state-path", str(quota)])

        assert _RecordingEngine.last_config.quota_state_path == Path(quota)

    @staticmethod
    def _hooks_module(name: str, hooks_cls):
        import types

        mod = types.ModuleType(name)
        mod.HOOKS_CLASS = hooks_cls
        return mod

    def test_run_two_hooks_called_in_order(self, tmp_path):
        """Refs sumipan/nexus#7095 AC3."""
        exec_md = tmp_path / "exec.jsonl"
        exec_md.write_text("")
        calls: list[str] = []

        class HookA:
            def on_task_success(self, uuid, task, metrics):
                calls.append("a")

        class HookB:
            def on_task_success(self, uuid, task, metrics):
                calls.append("b")

        mod_a = self._hooks_module("hook_a", HookA)
        mod_b = self._hooks_module("hook_b", HookB)

        def import_side_effect(name):
            if name == "hook_a":
                return mod_a
            if name == "hook_b":
                return mod_b
            raise ImportError(name)

        with patch("ghdag.dag.engine.DagEngine", _RecordingEngine), \
             patch("ghdag.dag.models.DagConfig", _capture_config), \
             patch("importlib.import_module", side_effect=import_side_effect):
            from ghdag.cli import main
            main(["run", str(exec_md), "--hooks", "hook_a", "--hooks", "hook_b"])

        hooks = _RecordingEngine.last_hooks
        from ghdag.cli.commands.run import _HookChain

        assert isinstance(hooks, _HookChain)
        hooks.on_task_success("u", None, None)
        assert calls == ["a", "b"]

    def test_run_single_hooks_passed_unwrapped(self, tmp_path):
        """Refs sumipan/nexus#7095 AC4."""
        exec_md = tmp_path / "exec.jsonl"
        exec_md.write_text("")

        class MyHooks:
            def on_task_success(self, uuid, task, metrics):
                pass

        mod = self._hooks_module("hook_single", MyHooks)

        with patch("ghdag.dag.engine.DagEngine", _RecordingEngine), \
             patch("ghdag.dag.models.DagConfig", _capture_config), \
             patch("importlib.import_module", return_value=mod):
            from ghdag.cli import main
            main(["run", str(exec_md), "--hooks", "hook_single"])

        from ghdag.cli.commands.run import _HookChain

        assert isinstance(_RecordingEngine.last_hooks, MyHooks)
        assert not isinstance(_RecordingEngine.last_hooks, _HookChain)

    def test_run_two_hooks_check_rejected_any_true(self, tmp_path):
        """Refs sumipan/nexus#7095 AC5."""
        exec_md = tmp_path / "exec.jsonl"
        exec_md.write_text("")

        class HookA:
            def check_rejected(self, result_path):
                return False

        class HookB:
            def check_rejected(self, result_path):
                return True

        mod_a = self._hooks_module("hook_a", HookA)
        mod_b = self._hooks_module("hook_b", HookB)

        def import_side_effect(name):
            if name == "hook_a":
                return mod_a
            if name == "hook_b":
                return mod_b
            raise ImportError(name)

        with patch("ghdag.dag.engine.DagEngine", _RecordingEngine), \
             patch("ghdag.dag.models.DagConfig", _capture_config), \
             patch("importlib.import_module", side_effect=import_side_effect):
            from ghdag.cli import main
            main(["run", str(exec_md), "--hooks", "hook_a", "--hooks", "hook_b"])

        assert _RecordingEngine.last_hooks.check_rejected("/any/result.md") is True

    def test_run_two_hooks_check_pipeline_status_first_non_none(self, tmp_path):
        """Refs sumipan/nexus#7095 AC6."""
        exec_md = tmp_path / "exec.jsonl"
        exec_md.write_text("")

        class HookA:
            def check_pipeline_status(self, result_path):
                return None

        class HookB:
            def check_pipeline_status(self, result_path):
                return "X_FAILED"

        mod_a = self._hooks_module("hook_a", HookA)
        mod_b = self._hooks_module("hook_b", HookB)

        def import_side_effect(name):
            if name == "hook_a":
                return mod_a
            if name == "hook_b":
                return mod_b
            raise ImportError(name)

        with patch("ghdag.dag.engine.DagEngine", _RecordingEngine), \
             patch("ghdag.dag.models.DagConfig", _capture_config), \
             patch("importlib.import_module", side_effect=import_side_effect):
            from ghdag.cli import main
            main(["run", str(exec_md), "--hooks", "hook_a", "--hooks", "hook_b"])

        assert _RecordingEngine.last_hooks.check_pipeline_status("/any/result.md") == "X_FAILED"

    def test_run_hooks_missing_second_module_exits_with_name(self, tmp_path, capsys):
        """Refs sumipan/nexus#7095 AC7."""
        exec_md = tmp_path / "exec.jsonl"
        exec_md.write_text("")
        _RecordingEngine.ran = False

        class HookA:
            def on_task_success(self, uuid, task, metrics):
                pass

        mod_a = self._hooks_module("hook_a", HookA)

        def import_side_effect(name):
            if name == "hook_a":
                return mod_a
            raise ImportError(f"No module named '{name}'")

        with patch("ghdag.dag.engine.DagEngine", _RecordingEngine), \
             patch("ghdag.dag.models.DagConfig", _capture_config), \
             patch("importlib.import_module", side_effect=import_side_effect):
            from ghdag.cli import main
            with pytest.raises(SystemExit) as exc:
                main(["run", str(exec_md), "--hooks", "hook_a", "--hooks", "no.such.module"])

        assert exc.value.code == 1
        assert "no.such.module" in capsys.readouterr().err
        assert _RecordingEngine.ran is False
