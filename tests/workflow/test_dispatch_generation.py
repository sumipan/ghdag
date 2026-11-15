"""Tests for generation-aware idempotency keys in WorkflowDispatcher."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from ghdag.core.models.workflow import HandlerConfig, StepConfig, TriggerConfig, WorkflowConfig
from ghdag.github_client import GitHubIssuePort
from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
from ghdag.pipeline.order import TemplateOrderBuilder
from ghdag.pipeline.state import PipelineState, build_idempotency_key
from ghdag.workflow.dispatcher import WorkflowDispatcher


def _make_workflow(name: str = "issuesmith") -> WorkflowConfig:
    return WorkflowConfig(
        name=name,
        triggers=[TriggerConfig(label="develop-ready", handler="impl")],
        handlers={
            "impl": HandlerConfig(
                steps=[StepConfig(id="p1", template="p1", model="claude-opus-4-6")],
            ),
        },
        polling_interval=0,
    )


def _make_dispatcher(tmp_path: Path) -> tuple[WorkflowDispatcher, PipelineState, MagicMock]:
    exec_jsonl = tmp_path / "jobs" / "exec.jsonl"
    exec_jsonl.parent.mkdir(parents=True)
    exec_jsonl.write_text("", encoding="utf-8")
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "p1.md").write_text("order ${issue_number}", encoding="utf-8")
    state = PipelineState(state_dir=tmp_path / ".pipeline-state", exec_jsonl_path=exec_jsonl)
    pipeline = LLMPipelineAPI(
        pipeline_state=state,
        order_builder=TemplateOrderBuilder(str(templates)),
        queue_dir=str(exec_jsonl.parent),
    )
    github = MagicMock(spec=GitHubIssuePort)
    github.get_issue_comments.return_value = []
    dispatcher = WorkflowDispatcher(
        workflows=[_make_workflow()],
        github_client=github,
        pipeline=pipeline,
        queue_dir=str(exec_jsonl.parent),
    )
    return dispatcher, state, github


class TestBuildIdempotencyKey:
    def test_generation_zero_uses_legacy_format(self):
        assert build_idempotency_key("issuesmith", "impl", 2876, 0) == "issuesmith:impl:2876"

    def test_generation_one_appends_suffix(self):
        assert build_idempotency_key("issuesmith", "impl", 2876, 1) == "issuesmith:impl:2876:1"

    def test_generation_two_appends_suffix(self):
        assert build_idempotency_key("issuesmith", "impl", 2876, 2) == "issuesmith:impl:2876:2"


class TestDispatchGeneration:
    def test_second_dispatch_is_skipped(self, tmp_path):
        dispatcher, state, github = _make_dispatcher(tmp_path)
        workflow = _make_workflow()
        handler = workflow.handlers["impl"]
        trigger = workflow.triggers[0]
        issue = {"number": 2876, "labels": []}

        result1 = dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)
        assert result1.status == "dispatched"

        result2 = dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)
        assert result2.status == "skipped"
        assert result2.reason == "already dispatched"
