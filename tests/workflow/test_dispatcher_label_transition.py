"""Tests for dispatcher label transition — Issue #2258 (A: 区切り文字非依存化)."""

from __future__ import annotations

from unittest.mock import MagicMock

from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
from ghdag.workflow.dispatcher import WorkflowDispatcher
from ghdag.github_client import GitHubIssuePort
from ghdag.workflow.schema import (
    HandlerConfig,
    StepConfig,
    TriggerConfig,
    WorkflowConfig,
)


def _make_workflow(trigger_labels: list[str]) -> WorkflowConfig:
    triggers = [TriggerConfig(label=lb, handler="brushup") for lb in trigger_labels]
    handlers = {
        "brushup": HandlerConfig(steps=[StepConfig(template="brushup", model="claude-opus-4-6")])
    }
    return WorkflowConfig(name="wf", triggers=triggers, handlers=handlers, polling_interval=30)


def _make_dispatcher(workflow: WorkflowConfig):
    github_client = MagicMock(spec=GitHubIssuePort)
    github_client.get_issue_comments.return_value = []
    pipeline_state = MagicMock()
    pipeline_state.check_idempotency.return_value = True
    pipeline_state.write_order_file.return_value = "ts-claude-order-uuid.md"
    order_builder = MagicMock()
    order_builder.build_order.return_value = "order content"
    pipeline = LLMPipelineAPI(
        pipeline_state=pipeline_state,
        order_builder=order_builder,
        queue_dir="queue",
    )
    dispatcher = WorkflowDispatcher(
        workflows=[workflow],
        github_client=github_client,
        pipeline=pipeline,
        queue_dir="queue",
    )
    dispatcher._write_design_md = MagicMock()
    return dispatcher, github_client, pipeline_state


def _make_issue(number: int, labels: list[str]) -> dict:
    return {
        "number": number,
        "title": f"Issue {number}",
        "body": "body",
        "labels": [{"name": lb} for lb in labels],
        "url": f"https://github.com/owner/repo/issues/{number}",
    }


class TestDispatchHyphenLabelTransition:
    def test_dispatch_hyphen_ready_transitions_to_hyphen_running(self):
        """trigger.label=research-ready → update_label("research-ready", "research-running")"""
        workflow = _make_workflow(["research-ready"])
        dispatcher, github_client, _ = _make_dispatcher(workflow)
        issue = _make_issue(1, ["research-ready"])
        handler = workflow.handlers["brushup"]
        trigger = workflow.triggers[0]

        result = dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)

        assert result.status == "dispatched"
        github_client.update_label.assert_called_once_with(1, "research-ready", "research-running")

    def test_dispatch_label_without_ready_suffix_no_transition(self):
        """trigger.label=research-foo → update_label は呼ばれない"""
        workflow = _make_workflow(["research-foo"])
        dispatcher, github_client, _ = _make_dispatcher(workflow)
        issue = _make_issue(1, ["research-foo"])
        handler = workflow.handlers["brushup"]
        trigger = workflow.triggers[0]

        result = dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)

        assert result.status == "dispatched"
        github_client.update_label.assert_not_called()


class TestDispatchColonLabelTransition:
    def test_dispatch_colon_ready_transitions_to_colon_running(self):
        """trigger.label=research:ready → update_label("research:ready", "research:running")"""
        workflow = _make_workflow(["research:ready"])
        dispatcher, github_client, _ = _make_dispatcher(workflow)
        issue = _make_issue(2, ["research:ready"])
        handler = workflow.handlers["brushup"]
        trigger = workflow.triggers[0]

        result = dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)

        assert result.status == "dispatched"
        github_client.update_label.assert_called_once_with(2, "research:ready", "research:running")


class TestGetCurrentRunningRankLabelSeparator:
    def test_get_current_running_rank_recognizes_colon_running(self):
        """trigger.label=research:ready の running は research:running → rank 0 を返す"""
        workflow = _make_workflow(["research:ready"])
        dispatcher, _, _ = _make_dispatcher(workflow)
        issue = _make_issue(3, ["research:running"])

        rank = dispatcher._get_current_running_rank(issue, workflow)

        assert rank == 0

    def test_get_current_running_rank_still_recognizes_hyphen_running(self):
        """trigger.label=research-ready の running は research-running → rank 0 を返す（回帰防止）"""
        workflow = _make_workflow(["research-ready"])
        dispatcher, _, _ = _make_dispatcher(workflow)
        issue = _make_issue(4, ["research-running"])

        rank = dispatcher._get_current_running_rank(issue, workflow)

        assert rank == 0
