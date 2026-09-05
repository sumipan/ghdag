"""Tests for ghdag trigger --redispatch."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ghdag.workflow.schema import HandlerConfig, StepConfig, TriggerConfig, WorkflowConfig


def _make_workflow() -> WorkflowConfig:
    return WorkflowConfig(
        name="issuesmith",
        triggers=[TriggerConfig(label="develop-ready", handler="impl")],
        handlers={
            "impl": HandlerConfig(
                steps=[StepConfig(id="p1", template="p1", model="claude-opus-4-6")],
            ),
        },
        polling_interval=0,
    )


class TestTriggerRedispatch:
    def test_redispatch_increments_generation_and_dispatches(self, tmp_path, monkeypatch):
        from ghdag.cli.commands.trigger import cmd_trigger

        monkeypatch.chdir(tmp_path)
        exec_jsonl = tmp_path / "jobs" / "exec.jsonl"
        exec_jsonl.parent.mkdir(parents=True)
        exec_jsonl.write_text("", encoding="utf-8")
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        templates = workflows_dir / "templates"
        templates.mkdir()
        (templates / "p1.md").write_text("order ${issue_number}", encoding="utf-8")
        (workflows_dir / "issuesmith.yml").write_text(
            """
name: issuesmith
template_dir: templates
polling_interval: 30
triggers:
  - label: develop-ready
    handler: impl
handlers:
  impl:
    steps:
      - id: p1
        template: p1
        model: claude-opus-4-6
""".strip(),
            encoding="utf-8",
        )

        issue = {"number": 2876, "labels": [], "title": "test", "body": ""}
        mock_github = MagicMock()
        mock_github.get_issue.return_value = issue

        args = MagicMock()
        args.workflows_dir = str(workflows_dir)
        args.issue_number = 2876
        args.handler = "impl"
        args.exec_jsonl = str(exec_jsonl)
        args.workflow = None
        args.redispatch = False
        args.reason = None

        with patch("ghdag.github_client.create_github_client", return_value=mock_github):
            cmd_trigger(args)

        args.redispatch = False
        with patch("ghdag.github_client.create_github_client", return_value=mock_github):
            with pytest.raises(SystemExit) as exc_skip:
                cmd_trigger(args)
            assert exc_skip.value.code == 1

        args.redispatch = True
        args.reason = "design changed"
        with patch("ghdag.github_client.create_github_client", return_value=mock_github):
            cmd_trigger(args)

        content = exec_jsonl.read_text(encoding="utf-8")
        assert "issuesmith:impl:2876:1" in content

        gen_path = tmp_path / ".pipeline-state" / "generations.json"
        assert gen_path.exists()
        generations = json.loads(gen_path.read_text(encoding="utf-8"))
        assert generations["issuesmith:impl:2876"] == 1

    def test_redispatch_reason_recorded_in_audit(self, tmp_path, monkeypatch):
        from ghdag.cli.commands.trigger import cmd_trigger

        monkeypatch.chdir(tmp_path)
        exec_jsonl = tmp_path / "jobs" / "exec.jsonl"
        exec_jsonl.parent.mkdir(parents=True)
        exec_jsonl.write_text("", encoding="utf-8")
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        templates = workflows_dir / "templates"
        templates.mkdir()
        (templates / "p1.md").write_text("order ${issue_number}", encoding="utf-8")
        (workflows_dir / "issuesmith.yml").write_text(
            """
name: issuesmith
template_dir: templates
polling_interval: 30
triggers:
  - label: develop-ready
    handler: impl
handlers:
  impl:
    steps:
      - id: p1
        template: p1
        model: claude-opus-4-6
""".strip(),
            encoding="utf-8",
        )

        issue = {"number": 2876, "labels": [], "title": "test", "body": ""}
        mock_github = MagicMock()
        mock_github.get_issue.return_value = issue

        args = MagicMock()
        args.workflows_dir = str(workflows_dir)
        args.issue_number = 2876
        args.handler = "impl"
        args.exec_jsonl = str(exec_jsonl)
        args.workflow = None
        args.redispatch = True
        args.reason = "worktree lost"

        with patch("ghdag.github_client.create_github_client", return_value=mock_github):
            cmd_trigger(args)

        audit_path = exec_jsonl.parent / "audit.jsonl"
        assert audit_path.exists()
        records = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        redispatch_records = [r for r in records if r.get("event_type") == "redispatch"]
        assert len(redispatch_records) == 1
        assert redispatch_records[0]["reason"] == "worktree lost"
        assert redispatch_records[0]["generation"] == 1

    def test_redispatch_without_reason_uses_fallback(self, tmp_path):
        from ghdag.workflow.dispatcher import WorkflowDispatcher
        from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
        from ghdag.pipeline.order import TemplateOrderBuilder
        from ghdag.pipeline.state import PipelineState

        exec_jsonl = tmp_path / "jobs" / "exec.jsonl"
        exec_jsonl.parent.mkdir(parents=True)
        exec_jsonl.write_text("", encoding="utf-8")
        templates = tmp_path / "templates"
        templates.mkdir()
        (templates / "p1.md").write_text("order", encoding="utf-8")
        state = PipelineState(state_dir=tmp_path / ".pipeline-state", exec_jsonl_path=exec_jsonl)
        pipeline = LLMPipelineAPI(
            pipeline_state=state,
            order_builder=TemplateOrderBuilder(str(templates)),
            queue_dir=str(exec_jsonl.parent),
        )
        github = MagicMock()
        github.get_issue_comments.return_value = []
        dispatcher = WorkflowDispatcher(
            workflows=[_make_workflow()],
            github_client=github,
            pipeline=pipeline,
            queue_dir=str(exec_jsonl.parent),
        )
        workflow = _make_workflow()
        handler = workflow.handlers["impl"]
        issue = {"number": 2876, "labels": []}

        dispatcher.dispatch(
            issue, workflow, handler,
            trigger=workflow.triggers[0], trigger_rank=0,
            redispatch=True,
        )

        audit_path = exec_jsonl.parent / "audit.jsonl"
        records = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        redispatch = [r for r in records if r.get("event_type") == "redispatch"][0]
        assert redispatch["reason"] == "(no reason)"
