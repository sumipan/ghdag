"""Tests for ghdag.cli — AC1 - AC11.

Test approach:
- Parse validation via main(argv=[...])
- Mock DagEngine / WorkflowDispatcher; assert correct args
- Error cases assert SystemExit code
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


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
        tdir = workflows_dir / "templates"
        tdir.mkdir(exist_ok=True)
        (tdir / "brushup.md").write_text("# brushup\n", encoding="utf-8")
        (tdir / "p1-implement.md").write_text("# p1-implement\n", encoding="utf-8")

    def test_trigger_calls_dispatch(self, tmp_path, capsys):
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        self._write_workflow_yaml(workflows_dir)
        exec_md = tmp_path / "exec.jsonl"
        exec_md.write_text("")

        mock_dispatch_result = MagicMock()
        mock_dispatch_result.status = "dispatched"
        mock_dispatch_result.reason = ""
        mock_dispatch_result.exec_lines = [
            '{"uuid": "11111111-1111-1111-1111-111111111111", "command": "cat a"}',
            '{"uuid": "22222222-2222-2222-2222-222222222222", "command": "cat b"}',
        ]
        mock_github_client = MagicMock()
        mock_github_client.get_issue.return_value = {
            "number": 42,
            "title": "Test",
            "body": "body",
            "labels": [],
            "url": "https://example.com",
        }
        trigger_state = {"dispatch_issue": None}

        class FakeDispatcher:
            def __init__(self, *args, **kwargs):
                pass

            def dispatch(self, issue, *args, **kwargs):
                trigger_state["dispatch_issue"] = issue
                return mock_dispatch_result

        with patch("ghdag.workflow.dispatcher.WorkflowDispatcher", FakeDispatcher), \
             patch("ghdag.github_client.create_github_client", return_value=mock_github_client), \
             patch("ghdag.pipeline.state.PipelineState"), \
             patch("ghdag.pipeline.order.TemplateOrderBuilder"):
            from ghdag.cli import main
            main([
                "trigger", "42",
                "--handler", "brushup",
                "--workflows-dir", str(workflows_dir),
                "--exec-md", str(exec_md),
            ])

        assert trigger_state["dispatch_issue"]["number"] == 42
        out = capsys.readouterr().out
        assert "exec_lines: 2 step(s)" in out

    def test_trigger_prints_zero_steps_when_exec_lines_empty(self, tmp_path, capsys):
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        self._write_workflow_yaml(workflows_dir)
        exec_md = tmp_path / "exec.jsonl"
        exec_md.write_text("")

        mock_dispatch_result = MagicMock()
        mock_dispatch_result.status = "dispatched"
        mock_dispatch_result.reason = ""
        mock_dispatch_result.exec_lines = []
        mock_github_client = MagicMock()
        mock_github_client.get_issue.return_value = {
            "number": 7, "title": "T", "body": "", "labels": [], "url": "",
        }

        class FakeDispatcher:
            def __init__(self, *args, **kwargs):
                pass

            def dispatch(self, *args, **kwargs):
                return mock_dispatch_result

        with patch("ghdag.workflow.dispatcher.WorkflowDispatcher", FakeDispatcher), \
             patch("ghdag.github_client.create_github_client", return_value=mock_github_client), \
             patch("ghdag.pipeline.state.PipelineState"), \
             patch("ghdag.pipeline.order.TemplateOrderBuilder"):
            from ghdag.cli import main
            main([
                "trigger", "7",
                "--handler", "brushup",
                "--workflows-dir", str(workflows_dir),
                "--exec-md", str(exec_md),
            ])

        out = capsys.readouterr().out
        assert "exec_lines: 0 step(s)" in out

    def test_trigger_with_workflow_option(self, tmp_path):
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        self._write_workflow_yaml(workflows_dir)
        exec_md = tmp_path / "exec.jsonl"
        exec_md.write_text("")

        mock_dispatch_result = MagicMock()
        mock_dispatch_result.status = "dispatched"
        mock_dispatch_result.reason = ""
        mock_dispatch_result.exec_lines = [
            '{"uuid": "33333333-3333-3333-3333-333333333333", "command": "cat"}',
        ]
        mock_github_client = MagicMock()
        mock_github_client.get_issue.return_value = {
            "number": 10, "title": "T", "body": "", "labels": [], "url": "",
        }
        trigger_state = {"dispatched": False}

        class FakeDispatcher:
            def __init__(self, *args, **kwargs):
                pass

            def dispatch(self, *args, **kwargs):
                trigger_state["dispatched"] = True
                return mock_dispatch_result

        with patch("ghdag.workflow.dispatcher.WorkflowDispatcher", FakeDispatcher), \
             patch("ghdag.github_client.create_github_client", return_value=mock_github_client), \
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

        assert trigger_state["dispatched"] is True


# ---------------------------------------------------------------------------
# AC10: ghdag trigger error cases
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
        tdir = workflows_dir / "templates"
        tdir.mkdir(exist_ok=True)
        (tdir / "brushup.md").write_text("# brushup\n", encoding="utf-8")

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
        """exit 1 when dispatch returns skipped"""
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        self._write_workflow_yaml(workflows_dir)
        exec_md = tmp_path / "exec.jsonl"
        exec_md.write_text("")

        mock_dispatch_result = MagicMock()
        mock_dispatch_result.status = "skipped"
        mock_dispatch_result.reason = "already dispatched"
        mock_dispatch_result.exec_lines = []
        mock_dispatcher_cls = MagicMock()
        mock_dispatcher_cls.return_value.dispatch.return_value = mock_dispatch_result
        mock_github_client = MagicMock()
        mock_github_client.get_issue.return_value = {
            "number": 42, "title": "T", "body": "", "labels": [], "url": "",
        }
        mock_create_github_client = MagicMock(return_value=mock_github_client)

        with patch("ghdag.workflow.dispatcher.WorkflowDispatcher", mock_dispatcher_cls), \
             patch("ghdag.github_client.create_github_client", mock_create_github_client), \
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
