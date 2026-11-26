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

from ghdag.core.models.metrics import TaskMetrics
from ghdag.dag.models import DagConfig as RealDagConfig


def _capture_config(**kwargs):
    """Build a real DagConfig while recording kwargs for CLI wiring tests."""
    _capture_config.last_kwargs = kwargs  # type: ignore[attr-defined]
    return RealDagConfig(**kwargs)


_capture_config.last_kwargs = {}


@pytest.fixture(autouse=True)
def _run_recorder_enabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Host env may set GHDAG_RUN_RECORDER=0; unit tests assume recorder on."""
    monkeypatch.setenv("GHDAG_RUN_RECORDER", "1")


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

        from ghdag.cli.commands.run import _HookChain

        assert isinstance(_RecordingEngine.last_hooks, _HookChain)

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

    def test_run_exits_1_on_invalid_nodes_file(self, tmp_path: Path) -> None:
        """Refs sumipan/ghdag#7651 — duplicate node names in --nodes exit 1."""
        exec_md = tmp_path / "exec.jsonl"
        exec_md.write_text("")
        nodes = tmp_path / "nodes.yml"
        nodes.write_text(
            "nodes:\n"
            "  - {name: dup, labels: [a], ssh: h1, workdir: /w}\n"
            "  - {name: dup, labels: [b], ssh: h2, workdir: /w2}\n",
            encoding="utf-8",
        )
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["run", str(exec_md), "--nodes", str(nodes)])
        assert exc.value.code == 1


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
        sample_metrics = TaskMetrics(
            uuid="u",
            engine=None,
            model=None,
            wall_time_sec=0.0,
            token_count=None,
            status="success",
            started_at=0.0,
            finished_at=0.0,
        )
        hooks.on_task_success("u", None, sample_metrics)
        assert calls == ["a", "b"]

    def test_run_single_partial_hook_is_wrapped_with_default_checks(self, tmp_path):
        """Refs sumipan/ghdag#8060 AC2 — recorder on wraps a partial custom hook."""
        exec_md = tmp_path / "exec.jsonl"
        exec_md.write_text("")

        class PartialHooks:
            def on_task_success(self, uuid, task, metrics):
                pass

        mod = self._hooks_module("hook_partial", PartialHooks)

        with patch("ghdag.dag.engine.DagEngine", _RecordingEngine), \
             patch("ghdag.dag.models.DagConfig", _capture_config), \
             patch("importlib.import_module", return_value=mod):
            from ghdag.cli import main
            main(["run", str(exec_md), "--hooks", "hook_partial"])

        from ghdag.cli.commands.run import _HookChain
        from ghdag.dag import _util

        hooks = _RecordingEngine.last_hooks
        assert isinstance(hooks, _HookChain)
        result_path = str(tmp_path / "pipeline-result.md")
        assert hooks.check_pipeline_status(result_path) == _util.check_pipeline_status(
            result_path,
        )
        for method in (
            "on_task_start",
            "on_task_success",
            "on_task_failure",
            "on_task_rejected",
            "on_task_dep_failed",
            "on_task_empty_result",
            "on_task_cancelled",
            "on_task_progress",
            "on_shutdown",
            "check_rejected",
            "check_pipeline_status",
            "check_promote_target",
        ):
            assert callable(getattr(hooks, method))

    def test_run_single_hook_is_unwrapped_when_recorder_disabled(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch,
    ):
        """Refs sumipan/ghdag#8060 AC3 — GHDAG_RUN_RECORDER=0 passes hook unwrapped."""
        monkeypatch.setenv("GHDAG_RUN_RECORDER", "0")
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
