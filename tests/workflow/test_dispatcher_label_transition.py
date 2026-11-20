"""Tests for dispatcher label transition — Issue #2258 (A: delimiter-agnostic)."""

from __future__ import annotations

from unittest.mock import MagicMock

from ghdag.core.models.workflow import (
    HandlerConfig,
    StepConfig,
    TriggerConfig,
    WorkflowConfig,
)
from ghdag.core.ports.forge import ForgePort
from ghdag.pipeline.task_queue import TaskQueue
from ghdag.workflow.dispatcher import WorkflowDispatcher


def _make_workflow(trigger_labels: list[str]) -> WorkflowConfig:
    triggers = [TriggerConfig(label=lb, handler="brushup") for lb in trigger_labels]
    handlers = {
        "brushup": HandlerConfig(steps=[StepConfig(template="brushup", model="claude-opus-4-6")])
    }
    return WorkflowConfig(name="wf", triggers=triggers, handlers=handlers, polling_interval=30)


def _make_dispatcher(workflow: WorkflowConfig):
    github_client = MagicMock(spec=ForgePort)
    github_client.get_issue_comments.return_value = []
    order_builder = MagicMock()
    order_builder.build_order.return_value = "order content"
    task_queue = MagicMock(spec=TaskQueue)
    task_queue.check_idempotency.return_value = True
    task_queue.order_builder_for.return_value = order_builder
    task_queue.enqueue.return_value = ['{"uuid": "u"}']
    dispatcher = WorkflowDispatcher(
        workflows=[workflow],
        github_client=github_client,
        task_queue=task_queue,
        queue_dir="queue",
    )
    dispatcher._write_design_md = MagicMock()
    return dispatcher, github_client, task_queue


def _make_issue(number: int, labels: list[str]) -> dict:
    return {
        "number": number,
        "title": f"Issue {number}",
        "body": "body",
        "labels": [{"name": lb} for lb in labels],
        "url": f"https://github.com/owner/repo/issues/{number}",
    }


class TestDispatchHyphenLabelTransition:
    def test_dispatch_label_without_ready_suffix_no_transition(self):
        """trigger.label=research-foo → update_label is not called"""
        workflow = _make_workflow(["research-foo"])
        dispatcher, github_client, _ = _make_dispatcher(workflow)
        issue = _make_issue(1, ["research-foo"])
        handler = workflow.handlers["brushup"]
        trigger = workflow.triggers[0]

        result = dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)

        assert result.status == "dispatched"
        github_client.update_label.assert_not_called()
