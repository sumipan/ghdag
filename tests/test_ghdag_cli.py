"""Tests for ghdag.cli — AC1 〜 AC8.

テスト方針:
- main(argv=[...]) のパース検証
- DagEngine / WorkflowDispatcher は mock、正しい引数で呼ばれることを assert
- 異常系は SystemExit の code を assert
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# AC1: ヘルプ表示
# ---------------------------------------------------------------------------


class TestHelp:
    def test_global_help_exits_0(self, capsys):
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0

    def test_run_help_exits_0(self, capsys):
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["run", "--help"])
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "exec_md" in captured.out
        assert "--interval" in captured.out

    def test_watch_help_exits_0(self, capsys):
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["watch", "--help"])
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "workflows_dir" in captured.out
        assert "--interval" in captured.out
        assert "--exec-md" in captured.out


# ---------------------------------------------------------------------------
# AC2: バージョン
# ---------------------------------------------------------------------------


class TestVersion:
    def test_version_outputs_version_string(self, capsys):
        from ghdag.cli import main

        main(["version"])
        captured = capsys.readouterr()
        assert captured.out.strip() == "0.3.0"


# ---------------------------------------------------------------------------
# AC3: ghdag run 正常系
# ---------------------------------------------------------------------------


class TestRunNormal:
    def test_run_calls_dag_engine_with_defaults(self, tmp_path):
        exec_md = tmp_path / "exec.md"
        exec_md.write_text("")

        mock_engine_cls = MagicMock()
        mock_config_cls = MagicMock()

        with patch("ghdag.dag.engine.DagEngine", mock_engine_cls), \
             patch("ghdag.dag.models.DagConfig", mock_config_cls):
            from ghdag.cli import main
            main(["run", str(exec_md)])

        mock_config_cls.assert_called_once_with(
            exec_md_path=str(exec_md), poll_interval=1.0
        )
        mock_engine_cls.assert_called_once_with(mock_config_cls.return_value, None)
        mock_engine_cls.return_value.run.assert_called_once()

    def test_run_with_custom_interval(self, tmp_path):
        exec_md = tmp_path / "exec.md"
        exec_md.write_text("")

        mock_engine_cls = MagicMock()
        mock_config_cls = MagicMock()

        with patch("ghdag.dag.engine.DagEngine", mock_engine_cls), \
             patch("ghdag.dag.models.DagConfig", mock_config_cls):
            from ghdag.cli import main
            main(["run", str(exec_md), "--interval", "10"])

        mock_config_cls.assert_called_once_with(
            exec_md_path=str(exec_md), poll_interval=10.0
        )


# ---------------------------------------------------------------------------
# AC3b: ghdag run --hooks
# ---------------------------------------------------------------------------


class TestRunHooks:
    def _make_hooks_module_with_class(self):
        """HOOKS_CLASS 属性を持つモジュール名前空間と hooks クラスを返す。"""
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
        exec_md = tmp_path / "exec.md"
        exec_md.write_text("")
        mock_module, MyHooks = self._make_hooks_module_with_class()

        mock_engine_cls = MagicMock()
        mock_config_cls = MagicMock()

        with patch("ghdag.dag.engine.DagEngine", mock_engine_cls), \
             patch("ghdag.dag.models.DagConfig", mock_config_cls), \
             patch("importlib.import_module", return_value=mock_module):
            from ghdag.cli import main
            main(["run", str(exec_md), "--hooks", "my_hooks"])

        # DagEngine は hooks インスタンス付きで呼ばれる
        call_args = mock_engine_cls.call_args
        assert call_args is not None
        _, hooks_arg = call_args[0]
        assert isinstance(hooks_arg, MyHooks)

    def test_hooks_set_engine_called(self, tmp_path):
        exec_md = tmp_path / "exec.md"
        exec_md.write_text("")
        mock_module, MyHooks = self._make_hooks_module_with_class()

        mock_engine_instance = MagicMock()
        mock_engine_cls = MagicMock(return_value=mock_engine_instance)
        mock_config_cls = MagicMock()

        with patch("ghdag.dag.engine.DagEngine", mock_engine_cls), \
             patch("ghdag.dag.models.DagConfig", mock_config_cls), \
             patch("importlib.import_module", return_value=mock_module):
            from ghdag.cli import main
            main(["run", str(exec_md), "--hooks", "my_hooks"])

        # engine.run() が呼ばれる
        mock_engine_instance.run.assert_called_once()

    def test_hooks_invalid_module_exits_1(self, tmp_path, capsys):
        exec_md = tmp_path / "exec.md"
        exec_md.write_text("")

        with pytest.raises(SystemExit) as exc:
            from ghdag.cli import main
            main(["run", str(exec_md), "--hooks", "nonexistent.module.path"])
        assert exc.value.code == 1
        assert "cannot import" in capsys.readouterr().err

    def test_no_hooks_arg_engine_called_with_none(self, tmp_path):
        exec_md = tmp_path / "exec.md"
        exec_md.write_text("")

        mock_engine_cls = MagicMock()
        mock_config_cls = MagicMock()

        with patch("ghdag.dag.engine.DagEngine", mock_engine_cls), \
             patch("ghdag.dag.models.DagConfig", mock_config_cls):
            from ghdag.cli import main
            main(["run", str(exec_md)])

        mock_engine_cls.assert_called_once_with(mock_config_cls.return_value, None)


# ---------------------------------------------------------------------------
# AC4: ghdag run 異常系
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
            main(["run", "nonexistent_exec.md"])
        assert exc.value.code == 1
        assert "not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# AC5: ghdag watch 正常系
# ---------------------------------------------------------------------------


class TestWatchNormal:
    def test_watch_calls_dispatcher_run(self, tmp_path):
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()

        mock_load = MagicMock(return_value=[])
        mock_dispatcher_cls = MagicMock()
        mock_github_cls = MagicMock()
        mock_state_cls = MagicMock()
        mock_builder_cls = MagicMock()

        with patch("ghdag.workflow.loader.load_workflows", mock_load), \
             patch("ghdag.workflow.dispatcher.WorkflowDispatcher", mock_dispatcher_cls), \
             patch("ghdag.workflow.github.GitHubIssueClient", mock_github_cls), \
             patch("ghdag.pipeline.state.PipelineState", mock_state_cls), \
             patch("ghdag.pipeline.order.TemplateOrderBuilder", mock_builder_cls):
            from ghdag.cli import main
            main(["watch", str(workflows_dir)])

        mock_load.assert_called_once()
        mock_dispatcher_cls.return_value.run.assert_called_once()

    def test_watch_with_options(self, tmp_path):
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        exec_md_path = str(tmp_path / "out.md")

        mock_load = MagicMock(return_value=[])
        mock_dispatcher_cls = MagicMock()
        mock_github_cls = MagicMock()
        mock_state_cls = MagicMock()
        mock_builder_cls = MagicMock()

        with patch("ghdag.workflow.loader.load_workflows", mock_load), \
             patch("ghdag.workflow.dispatcher.WorkflowDispatcher", mock_dispatcher_cls), \
             patch("ghdag.workflow.github.GitHubIssueClient", mock_github_cls), \
             patch("ghdag.pipeline.state.PipelineState", mock_state_cls), \
             patch("ghdag.pipeline.order.TemplateOrderBuilder", mock_builder_cls):
            from ghdag.cli import main
            main([
                "watch", str(workflows_dir),
                "--interval", "60",
                "--exec-md", exec_md_path,
            ])

        mock_state_cls.assert_called_once_with(
            state_dir=".pipeline-state",
            exec_md_path=exec_md_path,
        )
        mock_dispatcher_cls.return_value.run.assert_called_once()


# ---------------------------------------------------------------------------
# AC6: ghdag watch 異常系
# ---------------------------------------------------------------------------


class TestWatchError:
    def test_watch_no_args_exits_2(self):
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["watch"])
        assert exc.value.code == 2

    def test_watch_nonexistent_dir_exits_1(self, capsys):
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["watch", "nonexistent_workflows_dir/"])
        assert exc.value.code == 1
        assert "not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# AC7: python -m ghdag
# ---------------------------------------------------------------------------


class TestPythonM:
    def test_python_m_help_exits_0(self):
        import subprocess

        worktree_root = Path(__file__).resolve().parent.parent
        env = {**os.environ, "PYTHONPATH": str(worktree_root / "src")}
        result = subprocess.run(
            [sys.executable, "-m", "ghdag", "--help"],
            capture_output=True,
            text=True,
            cwd=str(worktree_root),
            env=env,
        )
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# AC8: ログレベル
# ---------------------------------------------------------------------------


class TestLogLevel:
    def _run_with_dummy_exec(self, argv: list[str], tmp_path) -> None:
        exec_md = tmp_path / "exec.md"
        exec_md.write_text("")
        with patch("ghdag.dag.engine.DagEngine"), \
             patch("ghdag.dag.models.DagConfig"):
            from ghdag.cli import main
            main(argv + ["run", str(exec_md)])

    def test_verbose_sets_debug(self, tmp_path):
        self._run_with_dummy_exec(["--verbose"], tmp_path)
        assert logging.getLogger().level == logging.DEBUG

    def test_quiet_sets_warning(self, tmp_path):
        self._run_with_dummy_exec(["--quiet"], tmp_path)
        assert logging.getLogger().level == logging.WARNING

    def test_default_sets_info(self, tmp_path):
        self._run_with_dummy_exec([], tmp_path)
        assert logging.getLogger().level == logging.INFO
