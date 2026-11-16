"""Tests for WorkflowDispatcher rate limit observation.

Each polling cycle observes rate-limit headers and records them in audit.jsonl.
Emit a warning when remaining is at or below the threshold. On fetch failure, continue silently.
"""

from __future__ import annotations

import logging
from pathlib import Path
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


def _make_workflow(name: str = "wf") -> WorkflowConfig:
    return WorkflowConfig(
        name=name,
        triggers=[TriggerConfig(label="wf:ready", handler="h")],
        handlers={
            "h": HandlerConfig(
                steps=[StepConfig(template="t", model="claude-opus-4-6")],
            ),
        },
        polling_interval=0,
    )


def _make_dispatcher(tmp_path: Path) -> tuple[WorkflowDispatcher, MagicMock]:
    github_client = MagicMock(spec=GitHubIssuePort)
    github_client.list_all_issues.return_value = []
    github_client.get_last_rate_limit.return_value = None
    task_queue = MagicMock(spec=TaskQueue)
    dispatcher = WorkflowDispatcher(
        workflows=[_make_workflow()],
        github_client=github_client,
        task_queue=task_queue,
        queue_dir=str(tmp_path),
    )
    return dispatcher, github_client


class TestRateLimitAudit:
    def test_warning_at_exact_threshold(self, tmp_path, caplog):
        """AC2 boundary: remaining=100 emits a warning."""
        dispatcher, github_client = _make_dispatcher(tmp_path)
        github_client.get_last_rate_limit.return_value = {
            "limit": 5000,
            "remaining": 100,
            "reset": 1700000000,
        }

        with caplog.at_level(logging.WARNING, logger="ghdag.workflow.dispatcher"):
            dispatcher._observe_rate_limit()

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_records) == 1

    def test_silent_continue_on_incomplete_rate_limit(self, tmp_path, caplog):
        """AC3 error case 2: get_last_rate_limit() returns incomplete dict → no audit write, no log."""
        dispatcher, github_client = _make_dispatcher(tmp_path)
        github_client.get_last_rate_limit.return_value = {"limit": 5000}  # no remaining

        with caplog.at_level(logging.WARNING, logger="ghdag.workflow.dispatcher"):
            dispatcher._observe_rate_limit()

        audit_path = tmp_path / "audit.jsonl"
        assert not audit_path.exists()
        assert len(caplog.records) == 0
