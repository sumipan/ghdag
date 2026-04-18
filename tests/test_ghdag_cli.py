"""Tests for ghdag.cli — AC1 〜 AC11.

テスト方針:
- main(argv=[...]) のパース検証
- DagEngine / WorkflowDispatcher は mock、正しい引数で呼ばれることを assert
- 異常系は SystemExit の code を assert
"""

from __future__ import annotations

import json
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
        assert captured.out.strip() == "0.7.3"


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

        call_kwargs = mock_config_cls.call_args[1]
        assert call_kwargs["exec_md_path"] == str(exec_md)
        assert call_kwargs["poll_interval"] == 1.0
        assert "cwd" in call_kwargs
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

        call_kwargs = mock_config_cls.call_args[1]
        assert call_kwargs["exec_md_path"] == str(exec_md)
        assert call_kwargs["poll_interval"] == 10.0
        assert "cwd" in call_kwargs


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


# ---------------------------------------------------------------------------
# AC9: ghdag trigger 正常系
# ---------------------------------------------------------------------------


class TestTriggerNormal:
    def _write_workflow_yaml(self, workflows_dir: Path) -> None:
        yaml_content = """\
name: test-pipeline
triggers:
  - label: "pipeline:draft-ready"
    handler: brushup
  - label: "pipeline:develop-ready"
    handler: impl
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-opus-4-6
  impl:
    steps:
      - id: p1
        template: p1-implement
        model: claude-sonnet-4-6
"""
        (workflows_dir / "test.yml").write_text(yaml_content, encoding="utf-8")

    def test_trigger_calls_dispatch(self, tmp_path):
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        self._write_workflow_yaml(workflows_dir)
        exec_md = tmp_path / "exec.md"
        exec_md.write_text("")

        mock_dispatch_result = MagicMock()
        mock_dispatch_result.status = "dispatched"
        mock_dispatch_result.reason = ""
        mock_dispatch_result.exec_lines = ["# idempotency: ...", "uuid: cat ..."]
        mock_dispatcher_cls = MagicMock()
        mock_dispatcher_cls.return_value.dispatch.return_value = mock_dispatch_result
        mock_github_cls = MagicMock()
        mock_github_cls.return_value.get_issue.return_value = {
            "number": 42,
            "title": "Test",
            "body": "body",
            "labels": [],
            "url": "https://example.com",
        }

        with patch("ghdag.workflow.dispatcher.WorkflowDispatcher", mock_dispatcher_cls), \
             patch("ghdag.workflow.github.GitHubIssueClient", mock_github_cls), \
             patch("ghdag.pipeline.state.PipelineState"), \
             patch("ghdag.pipeline.order.TemplateOrderBuilder"):
            from ghdag.cli import main
            main([
                "trigger", "42",
                "--handler", "brushup",
                "--workflows-dir", str(workflows_dir),
                "--exec-md", str(exec_md),
            ])

        mock_dispatcher_cls.return_value.dispatch.assert_called_once()
        call_args = mock_dispatcher_cls.return_value.dispatch.call_args
        assert call_args[0][0]["number"] == 42

    def test_trigger_with_workflow_option(self, tmp_path):
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        self._write_workflow_yaml(workflows_dir)
        exec_md = tmp_path / "exec.md"
        exec_md.write_text("")

        mock_dispatch_result = MagicMock()
        mock_dispatch_result.status = "dispatched"
        mock_dispatch_result.reason = ""
        mock_dispatch_result.exec_lines = ["# idempotency: ..."]
        mock_dispatcher_cls = MagicMock()
        mock_dispatcher_cls.return_value.dispatch.return_value = mock_dispatch_result
        mock_github_cls = MagicMock()
        mock_github_cls.return_value.get_issue.return_value = {
            "number": 10, "title": "T", "body": "", "labels": [], "url": "",
        }

        with patch("ghdag.workflow.dispatcher.WorkflowDispatcher", mock_dispatcher_cls), \
             patch("ghdag.workflow.github.GitHubIssueClient", mock_github_cls), \
             patch("ghdag.pipeline.state.PipelineState"), \
             patch("ghdag.pipeline.order.TemplateOrderBuilder"):
            from ghdag.cli import main
            main([
                "trigger", "10",
                "--handler", "impl",
                "--workflow", "test-pipeline",
                "--workflows-dir", str(workflows_dir),
                "--exec-md", str(exec_md),
            ])

        mock_dispatcher_cls.return_value.dispatch.assert_called_once()


# ---------------------------------------------------------------------------
# AC10: ghdag trigger 異常系
# ---------------------------------------------------------------------------


class TestTriggerError:
    def _write_workflow_yaml(self, workflows_dir: Path) -> None:
        yaml_content = """\
name: test-pipeline
triggers:
  - label: "pipeline:draft-ready"
    handler: brushup
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-opus-4-6
"""
        (workflows_dir / "test.yml").write_text(yaml_content, encoding="utf-8")

    def test_trigger_no_args_exits_2(self):
        from ghdag.cli import main
        with pytest.raises(SystemExit) as exc:
            main(["trigger"])
        assert exc.value.code == 2

    def test_trigger_missing_handler_exits_2(self):
        from ghdag.cli import main
        with pytest.raises(SystemExit) as exc:
            main(["trigger", "42"])
        assert exc.value.code == 2

    def test_trigger_nonexistent_dir_exits_1(self, capsys):
        from ghdag.cli import main
        with pytest.raises(SystemExit) as exc:
            main([
                "trigger", "42",
                "--handler", "brushup",
                "--workflows-dir", "nonexistent/",
            ])
        assert exc.value.code == 1
        assert "not found" in capsys.readouterr().err

    def test_trigger_unknown_handler_exits_1(self, tmp_path, capsys):
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        self._write_workflow_yaml(workflows_dir)

        from ghdag.cli import main
        with pytest.raises(SystemExit) as exc:
            main([
                "trigger", "42",
                "--handler", "nonexistent",
                "--workflows-dir", str(workflows_dir),
            ])
        assert exc.value.code == 1
        assert "not found" in capsys.readouterr().err

    def test_trigger_unknown_workflow_exits_1(self, tmp_path, capsys):
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        self._write_workflow_yaml(workflows_dir)

        from ghdag.cli import main
        with pytest.raises(SystemExit) as exc:
            main([
                "trigger", "42",
                "--handler", "brushup",
                "--workflow", "wrong-name",
                "--workflows-dir", str(workflows_dir),
            ])
        assert exc.value.code == 1
        assert "not found" in capsys.readouterr().err

    def test_trigger_skipped_exits_1(self, tmp_path, capsys):
        """dispatch が skipped を返した場合は exit 1"""
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        self._write_workflow_yaml(workflows_dir)
        exec_md = tmp_path / "exec.md"
        exec_md.write_text("")

        mock_dispatch_result = MagicMock()
        mock_dispatch_result.status = "skipped"
        mock_dispatch_result.reason = "already dispatched"
        mock_dispatch_result.exec_lines = []
        mock_dispatcher_cls = MagicMock()
        mock_dispatcher_cls.return_value.dispatch.return_value = mock_dispatch_result
        mock_github_cls = MagicMock()
        mock_github_cls.return_value.get_issue.return_value = {
            "number": 42, "title": "T", "body": "", "labels": [], "url": "",
        }

        with patch("ghdag.workflow.dispatcher.WorkflowDispatcher", mock_dispatcher_cls), \
             patch("ghdag.workflow.github.GitHubIssueClient", mock_github_cls), \
             patch("ghdag.pipeline.state.PipelineState"), \
             patch("ghdag.pipeline.order.TemplateOrderBuilder"):
            from ghdag.cli import main
            with pytest.raises(SystemExit) as exc:
                main([
                    "trigger", "42",
                    "--handler", "brushup",
                    "--workflows-dir", str(workflows_dir),
                    "--exec-md", str(exec_md),
                ])
            assert exc.value.code == 1


# ---------------------------------------------------------------------------
# AC11: GitHubIssueClient.get_issue / dispatch_event
# ---------------------------------------------------------------------------


class TestGitHubIssueClientExtended:
    def test_get_issue_calls_subprocess(self):
        from ghdag.workflow.github import GitHubIssueClient
        client = GitHubIssueClient()
        mock_result = MagicMock()
        mock_result.stdout = json.dumps({
            "number": 42, "title": "Test", "body": "body",
            "labels": [{"name": "bug"}], "url": "https://example.com",
        })
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = client.get_issue(42)
        mock_run.assert_called_once_with(
            ["gh", "issue", "view", "42", "--json", "number,title,body,labels,url"],
            capture_output=True, text=True, check=True,
        )
        assert result["number"] == 42

    def test_dispatch_event_calls_subprocess(self):
        from ghdag.workflow.github import GitHubIssueClient
        client = GitHubIssueClient()
        with patch("subprocess.run") as mock_run:
            client.dispatch_event("pipeline-trigger", {"issue": 42})
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert "repos/:owner/:repo/dispatches" in call_args[0][0]
        assert call_args[1]["input"] is not None

    def test_dispatch_event_without_payload(self):
        from ghdag.workflow.github import GitHubIssueClient
        client = GitHubIssueClient()
        with patch("subprocess.run") as mock_run:
            client.dispatch_event("simple-event")
        input_data = json.loads(mock_run.call_args[1]["input"])
        assert input_data["event_type"] == "simple-event"
        assert "client_payload" not in input_data
