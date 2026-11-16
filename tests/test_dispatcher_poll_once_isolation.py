"""Tests for WorkflowDispatcher.poll_once exception isolation after batch fetch.

On bulk fetch (list_all_issues) failure, skip all triggers for that client.
Exceptions during label filtering skip at trigger granularity (per-trigger isolation).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from ghdag.core.models.workflow import (
    HandlerConfig,
    StepConfig,
    TriggerConfig,
    WorkflowConfig,
)
from ghdag.github_client import GitHubIssuePort
from ghdag.pipeline.task_queue import TaskQueue
from ghdag.workflow.dispatcher import WorkflowDispatcher


def _make_workflow(name: str, labels: list[str]) -> WorkflowConfig:
    triggers = [TriggerConfig(label=lb, handler="h") for lb in labels]
    return WorkflowConfig(
        name=name,
        triggers=triggers,
        handlers={
            "h": HandlerConfig(
                steps=[StepConfig(template="t", model="claude-opus-4-6")],
            ),
        },
        polling_interval=0,
    )


def _make_issue(number: int, label: str) -> dict:
    return {
        "number": number,
        "title": f"Issue {number}",
        "body": "body",
        "labels": [{"name": label}],
        "url": f"https://github.com/owner/repo/issues/{number}",
    }


def _make_dispatcher(workflows: list[WorkflowConfig]) -> tuple[WorkflowDispatcher, MagicMock]:
    github_client = MagicMock(spec=GitHubIssuePort)
    github_client.list_all_issues.return_value = []
    github_client.get_last_rate_limit.return_value = None
    task_queue = MagicMock(spec=TaskQueue)
    dispatcher = WorkflowDispatcher(
        workflows=workflows,
        github_client=github_client,
        task_queue=task_queue,
        queue_dir="queue",
    )
    return dispatcher, github_client


class TestPollOncePerTriggerIsolation:
    def test_filter_failure_in_one_issue_does_not_skip_subsequent(self):
        """A per-issue exception during label filtering must not stop other issues/triggers."""
        wf = _make_workflow("wf", ["a:ready", "b:ready"])
        dispatcher, github_client = _make_dispatcher([wf])

        bad = {"number": 1, "labels": "not-a-list"}  # labels is not an iterable of dict
        good_a = _make_issue(10, "a:ready")
        good_b = _make_issue(99, "b:ready")
        github_client.list_all_issues.return_value = [bad, good_a, good_b]

        results = dispatcher.poll_once()

        assert {r["issue"] for r in results} == {10, 99}

    def test_no_exception_propagates_when_batch_fails(self):
        """Bulk fetch failure must not propagate as an exception to the caller."""
        wf = _make_workflow("wf", ["a:ready", "b:ready"])
        dispatcher, github_client = _make_dispatcher([wf])
        github_client.list_all_issues.side_effect = RuntimeError("boom")

        results = dispatcher.poll_once()
        assert results == []
