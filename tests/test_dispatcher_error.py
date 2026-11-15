"""Tests for WorkflowDispatcher error handling — TC-7, TC-9 (Issue #396)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ghdag.core.models.workflow import (
    HandlerConfig,
    StepConfig,
    TriggerConfig,
    WorkflowConfig,
)
from ghdag.github_client import GitHubIssuePort
from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
from ghdag.workflow.dispatcher import WorkflowDispatcher


def _make_workflow() -> WorkflowConfig:
    return WorkflowConfig(
        name="test-pipeline",
        triggers=[TriggerConfig(label="pipeline:draft-ready", handler="brushup")],
        handlers={
            "brushup": HandlerConfig(
                steps=[StepConfig(template="brushup", model="claude-opus-4-6")],
            ),
        },
        polling_interval=0,
    )


def _make_dispatcher(workflow: WorkflowConfig) -> tuple[WorkflowDispatcher, MagicMock]:
    github_client = MagicMock(spec=GitHubIssuePort)
    github_client.list_all_issues.return_value = []
    github_client.get_last_rate_limit.return_value = None  # skip rate-limit observation
    github_client.get_rate_limit.return_value = None
    pipeline = MagicMock(spec=LLMPipelineAPI)
    dispatcher = WorkflowDispatcher(
        workflows=[workflow],
        github_client=github_client,
        pipeline=pipeline,
        queue_dir="queue",
    )
    return dispatcher, github_client


class TestTC9NonIntIssueNumberSkipsComment:
    def test_no_comment_when_issue_number_not_int(self):
        """TC-9: non-int issue_number does not crash"""
        workflow = _make_workflow()
        dispatcher, github_client = _make_dispatcher(workflow)

        handler = workflow.handlers["brushup"]
        trigger = workflow.triggers[0]
        broken_match = {
            "issue": "?",
            "workflow": workflow.name,
            "handler": "brushup",
            "_issue_data": {"number": "?", "title": "broken", "body": "", "labels": [], "url": ""},
            "_workflow": workflow,
            "_handler": handler,
            "_trigger": trigger,
            "_trigger_rank": 0,
        }
        dispatcher.poll_once = MagicMock(return_value=[broken_match])
        dispatcher.dispatch = MagicMock(side_effect=RuntimeError("error"))

        dispatcher.run(max_iterations=1)

        github_client.add_comment.assert_not_called()


def test_github_issue_port_spec_restricts_unknown_methods():
    github_client = MagicMock(spec=GitHubIssuePort)

    with pytest.raises(AttributeError):
        _ = github_client.non_port_method
