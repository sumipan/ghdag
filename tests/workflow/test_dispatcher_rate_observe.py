"""Tests for header-based rate limit observation (nexus #3070).

_observe_rate_limit は get_last_rate_limit() のみ使い、GET /rate_limit を呼ばない。
応答ヘッダ由来の remaining / limit / used / reset を audit に書く。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

from ghdag.github_client import GitHubIssuePort
from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
from ghdag.workflow.dispatcher import _RATE_LIMIT_THRESHOLD, WorkflowDispatcher
from ghdag.workflow.schema import (
    HandlerConfig,
    StepConfig,
    TriggerConfig,
    WorkflowConfig,
)


def _make_workflow(polling_interval: int = 30) -> WorkflowConfig:
    return WorkflowConfig(
        name="wf",
        triggers=[TriggerConfig(label="wf:ready", handler="h")],
        handlers={
            "h": HandlerConfig(
                steps=[StepConfig(template="t", model="claude-opus-4-6")],
            ),
        },
        polling_interval=polling_interval,
    )


def _make_dispatcher(tmp_path: Path, polling_interval: int = 30) -> tuple[WorkflowDispatcher, MagicMock]:
    github = MagicMock(spec=GitHubIssuePort)
    github.list_all_issues.return_value = []
    github.get_last_rate_limit.return_value = None
    github.get_rate_limit.return_value = None
    pipeline = MagicMock(spec=LLMPipelineAPI)
    dispatcher = WorkflowDispatcher(
        workflows=[_make_workflow(polling_interval)],
        github_client=github,
        pipeline=pipeline,
        queue_dir=str(tmp_path),
    )
    return dispatcher, github


class TestObserveUsesLastRateLimit:
    def test_records_header_rate_limit_in_audit(self, tmp_path):
        """AC-4: get_last_rate_limit の値（used 含む）が audit に記録される。"""
        dispatcher, github = _make_dispatcher(tmp_path)
        github.get_last_rate_limit.return_value = {
            "remaining": 2474,
            "limit": 5000,
            "used": 2526,
            "reset": 1789023299,
        }

        dispatcher._observe_rate_limit()

        github.get_rate_limit.assert_not_called()
        github.get_last_rate_limit.assert_called()
        audit_path = tmp_path / "audit.jsonl"
        records = [json.loads(line) for line in audit_path.read_text().splitlines()]
        rate_records = [r for r in records if r.get("event") == "github_rate_limit"]
        assert len(rate_records) == 1
        rec = rate_records[0]
        assert rec["remaining"] == 2474
        assert rec["limit"] == 5000
        assert rec["used"] == 2526
        assert rec["reset"] == 1789023299

    def test_does_not_call_get_rate_limit_api(self, tmp_path):
        dispatcher, github = _make_dispatcher(tmp_path)
        github.get_last_rate_limit.return_value = {
            "remaining": 1000,
            "limit": 5000,
            "used": 4000,
            "reset": 1700000000,
        }
        dispatcher._observe_rate_limit()
        github.get_rate_limit.assert_not_called()

    def test_none_last_rate_limit_is_silent(self, tmp_path, caplog):
        dispatcher, github = _make_dispatcher(tmp_path)
        github.get_last_rate_limit.return_value = None

        with caplog.at_level(logging.WARNING, logger="ghdag.workflow.dispatcher"):
            dispatcher._observe_rate_limit()

        assert not (tmp_path / "audit.jsonl").exists()
        assert len(caplog.records) == 0


class TestPollingIntervalBackoff:
    def test_doubles_interval_when_remaining_low(self, tmp_path, caplog):
        dispatcher, github = _make_dispatcher(tmp_path, polling_interval=30)
        assert dispatcher._base_polling_interval == 30
        assert dispatcher._current_polling_interval == 30

        github.get_last_rate_limit.return_value = {
            "remaining": _RATE_LIMIT_THRESHOLD,
            "limit": 5000,
            "used": 4900,
            "reset": 1700000000,
        }
        with caplog.at_level(logging.WARNING, logger="ghdag.workflow.dispatcher"):
            dispatcher._observe_rate_limit()

        assert dispatcher._current_polling_interval == 60
        assert any(r.levelno == logging.WARNING for r in caplog.records)

    def test_restores_interval_when_remaining_recovers(self, tmp_path):
        dispatcher, github = _make_dispatcher(tmp_path, polling_interval=30)
        github.get_last_rate_limit.return_value = {
            "remaining": 50,
            "limit": 5000,
            "used": 4950,
            "reset": 1700000000,
        }
        dispatcher._observe_rate_limit()
        assert dispatcher._current_polling_interval == 60

        github.get_last_rate_limit.return_value = {
            "remaining": 200,
            "limit": 5000,
            "used": 4800,
            "reset": 1700000000,
        }
        dispatcher._observe_rate_limit()
        assert dispatcher._current_polling_interval == 30
