"""Tests for WorkflowDispatcher rate limit observation.

Each polling cycle observes rate-limit headers and records them in audit.jsonl.
Emit a warning when remaining is at or below the threshold. On fetch failure, continue silently.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

from ghdag.github_client import GitHubIssuePort
from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
from ghdag.workflow.dispatcher import WorkflowDispatcher
from ghdag.workflow.schema import (
    HandlerConfig,
    StepConfig,
    TriggerConfig,
    WorkflowConfig,
)


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
    pipeline = MagicMock(spec=LLMPipelineAPI)
    dispatcher = WorkflowDispatcher(
        workflows=[_make_workflow()],
        github_client=github_client,
        pipeline=pipeline,
        queue_dir=str(tmp_path),
    )
    return dispatcher, github_client


class TestRateLimitAudit:
    def test_rate_limit_recorded_in_audit(self, tmp_path):
        """AC1: on successful get_last_rate_limit(), append a github_rate_limit event to audit.jsonl."""
        dispatcher, github_client = _make_dispatcher(tmp_path)
        github_client.get_last_rate_limit.return_value = {
            "limit": 5000,
            "remaining": 4800,
            "reset": 1700000000,
        }

        dispatcher._observe_rate_limit()

        audit_path = tmp_path / "audit.jsonl"
        assert audit_path.exists()
        records = [json.loads(line) for line in audit_path.read_text().splitlines()]
        rate_records = [r for r in records if r.get("event") == "github_rate_limit"]
        assert len(rate_records) == 1
        rec = rate_records[0]
        assert rec["remaining"] == 4800
        assert rec["limit"] == 5000
        assert rec["reset"] == 1700000000
        assert rec["correlation_id"] is None

    def test_warning_when_remaining_below_threshold(self, tmp_path, caplog):
        """AC2 happy path: remaining=50 emits a warning log."""
        dispatcher, github_client = _make_dispatcher(tmp_path)
        github_client.get_last_rate_limit.return_value = {
            "limit": 5000,
            "remaining": 50,
            "reset": 1700000000,
        }

        with caplog.at_level(logging.WARNING, logger="ghdag.workflow.dispatcher"):
            dispatcher._observe_rate_limit()

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_records) == 1
        assert "50" in warning_records[0].message
        assert "5000" in warning_records[0].message

    def test_no_warning_when_remaining_above_threshold(self, tmp_path, caplog):
        """AC2 boundary: remaining=101 does not emit a warning."""
        dispatcher, github_client = _make_dispatcher(tmp_path)
        github_client.get_last_rate_limit.return_value = {
            "limit": 5000,
            "remaining": 101,
            "reset": 1700000000,
        }

        with caplog.at_level(logging.WARNING, logger="ghdag.workflow.dispatcher"):
            dispatcher._observe_rate_limit()

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_records) == 0

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

    def test_silent_continue_on_get_rate_limit_failure(self, tmp_path, caplog):
        """AC3 error case 1: get_last_rate_limit() returns None → no audit write, no log."""
        dispatcher, github_client = _make_dispatcher(tmp_path)
        github_client.get_last_rate_limit.return_value = None

        with caplog.at_level(logging.WARNING, logger="ghdag.workflow.dispatcher"):
            dispatcher._observe_rate_limit()

        audit_path = tmp_path / "audit.jsonl"
        assert not audit_path.exists()
        assert len(caplog.records) == 0

    def test_silent_continue_on_incomplete_rate_limit(self, tmp_path, caplog):
        """AC3 error case 2: get_last_rate_limit() returns incomplete dict → no audit write, no log."""
        dispatcher, github_client = _make_dispatcher(tmp_path)
        github_client.get_last_rate_limit.return_value = {"limit": 5000}  # no remaining

        with caplog.at_level(logging.WARNING, logger="ghdag.workflow.dispatcher"):
            dispatcher._observe_rate_limit()

        audit_path = tmp_path / "audit.jsonl"
        assert not audit_path.exists()
        assert len(caplog.records) == 0

    def test_dispatch_proceeds_after_rate_limit_failure(self, tmp_path):
        """AC3: after get_last_rate_limit() fails, _observe_rate_limit() raises nothing and dispatch continues."""
        dispatcher, github_client = _make_dispatcher(tmp_path)
        github_client.get_last_rate_limit.return_value = None

        # _observe_rate_limit() must not raise
        dispatcher._observe_rate_limit()

        # dispatch must still be callable (mock present)
        github_client.list_all_issues.return_value = []
        matches = dispatcher.poll_once()
        assert matches == []
