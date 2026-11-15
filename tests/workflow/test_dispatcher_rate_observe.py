"""Tests for header-based rate limit observation (nexus #3070).

_observe_rate_limit uses only get_last_rate_limit(); it does not call GET /rate_limit.
Write remaining / limit / used / reset from response headers into the audit log.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from ghdag.core.models.workflow import HandlerConfig, StepConfig, TriggerConfig, WorkflowConfig
from ghdag.github_client import GitHubIssuePort
from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
from ghdag.workflow.dispatcher import WorkflowDispatcher


def _make_workflow(polling_interval: int = 30) -> WorkflowConfig:
    return WorkflowConfig(
        name="wf",
        triggers=[TriggerConfig(label="wf:ready", handler="h")],
        handlers={"h": HandlerConfig(steps=[StepConfig(template="t", model="claude-opus-4-6")])},
        polling_interval=polling_interval,
    )


def _make_dispatcher(tmp_path: Path, polling_interval: int = 30) -> tuple[WorkflowDispatcher, MagicMock]:
    github = MagicMock(spec=GitHubIssuePort)
    github.list_all_issues.return_value = []
    github.get_last_rate_limit.return_value = None
    github.get_rate_limit.return_value = None
    pipeline = MagicMock(spec=LLMPipelineAPI)
    dispatcher = WorkflowDispatcher(
        workflows=[_make_workflow(polling_interval)], github_client=github, pipeline=pipeline, queue_dir=str(tmp_path)
    )
    return (dispatcher, github)


class TestObserveUsesLastRateLimit:
    def test_does_not_call_get_rate_limit_api(self, tmp_path):
        dispatcher, github = _make_dispatcher(tmp_path)
        github.get_last_rate_limit.return_value = {"remaining": 1000, "limit": 5000, "used": 4000, "reset": 1700000000}
        dispatcher._observe_rate_limit()
        github.get_rate_limit.assert_not_called()
