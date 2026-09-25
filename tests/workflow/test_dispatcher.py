"""Tests for WorkflowDispatcher rate-limit poll skipping (nexus #3752)."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from ghdag.exceptions import RateLimitError
from ghdag.github_client import GitHubIssuePort
from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
from ghdag.workflow.dispatcher import WorkflowDispatcher
from ghdag.workflow.schema import (
    HandlerConfig,
    StepConfig,
    TriggerConfig,
    WorkflowConfig,
)


def _make_workflow() -> WorkflowConfig:
    return WorkflowConfig(
        name="wf",
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
    dispatcher = WorkflowDispatcher(
        workflows=[_make_workflow()],
        github_client=github_client,
        pipeline=MagicMock(spec=LLMPipelineAPI),
        queue_dir=str(tmp_path),
    )
    return dispatcher, github_client


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def test_once_rate_limited_logs_skip_line_without_raising(tmp_path, caplog):
    """AC-2: watch --once (max_iterations=1) logs one skip line and returns normally."""
    dispatcher, github = _make_dispatcher(tmp_path)
    reset_at = int(time.time()) + 600
    github.list_all_issues.side_effect = RateLimitError(
        "rate limited", status_code=403, reset_at=reset_at
    )

    with caplog.at_level(logging.INFO, logger="ghdag.workflow.dispatcher"):
        dispatcher.run(max_iterations=1)

    skip_lines = [
        r.getMessage() for r in caplog.records if "rate limited: skip poll until" in r.getMessage()
    ]
    assert skip_lines == [f"rate limited: skip poll until {_iso(reset_at)}"]
    assert dispatcher._rate_limited_until == reset_at


def test_poll_skipped_until_reset(tmp_path, caplog):
    """Subsequent iterations do not call the API before reset_at."""
    dispatcher, github = _make_dispatcher(tmp_path)
    reset_at = int(time.time()) + 600
    github.list_all_issues.side_effect = RateLimitError(
        "rate limited", status_code=403, reset_at=reset_at
    )

    with caplog.at_level(logging.INFO, logger="ghdag.workflow.dispatcher"):
        dispatcher.run(max_iterations=3)

    assert github.list_all_issues.call_count == 1
    skip_lines = [
        r for r in caplog.records if "rate limited: skip poll until" in r.getMessage()
    ]
    assert len(skip_lines) == 3


def test_poll_resumes_after_reset(tmp_path):
    """The first iteration past reset_at polls normally and clears the state."""
    dispatcher, github = _make_dispatcher(tmp_path)
    dispatcher._rate_limited_until = int(time.time()) - 1

    dispatcher.run(max_iterations=1)

    github.list_all_issues.assert_called_once_with("open")
    assert dispatcher._rate_limited_until is None


def test_rate_limit_without_reset_does_not_skip(tmp_path):
    dispatcher, github = _make_dispatcher(tmp_path)
    github.list_all_issues.side_effect = RateLimitError("rate limited", status_code=403)

    dispatcher.run(max_iterations=2)

    assert github.list_all_issues.call_count == 2
    assert dispatcher._rate_limited_until is None
