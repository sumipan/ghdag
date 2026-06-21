"""Tests for WorkflowDispatcher rate limit observation.

ポーリングサイクルごとに GitHub API rate limit を観測し audit.jsonl に記録する。
残量が閾値以下の場合に warning ログを出力する。取得失敗時はサイレントに続行する。
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
    github_client.list_issues.return_value = []
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
        """AC1: get_rate_limit() 成功時に audit.jsonl に github_rate_limit イベントが追記される。"""
        dispatcher, github_client = _make_dispatcher(tmp_path)
        github_client.get_rate_limit.return_value = {
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
        """AC2 正常系: remaining=50 のとき warning ログが出る。"""
        dispatcher, github_client = _make_dispatcher(tmp_path)
        github_client.get_rate_limit.return_value = {
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
        """AC2 境界値: remaining=101 のとき warning ログが出ない。"""
        dispatcher, github_client = _make_dispatcher(tmp_path)
        github_client.get_rate_limit.return_value = {
            "limit": 5000,
            "remaining": 101,
            "reset": 1700000000,
        }

        with caplog.at_level(logging.WARNING, logger="ghdag.workflow.dispatcher"):
            dispatcher._observe_rate_limit()

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_records) == 0

    def test_warning_at_exact_threshold(self, tmp_path, caplog):
        """AC2 境界値: remaining=100 のとき warning ログが出る。"""
        dispatcher, github_client = _make_dispatcher(tmp_path)
        github_client.get_rate_limit.return_value = {
            "limit": 5000,
            "remaining": 100,
            "reset": 1700000000,
        }

        with caplog.at_level(logging.WARNING, logger="ghdag.workflow.dispatcher"):
            dispatcher._observe_rate_limit()

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_records) == 1

    def test_silent_continue_on_get_rate_limit_failure(self, tmp_path, caplog):
        """AC3 異常系1: get_rate_limit() が None を返す場合、audit 書き込みなし・ログなし。"""
        dispatcher, github_client = _make_dispatcher(tmp_path)
        github_client.get_rate_limit.return_value = None

        with caplog.at_level(logging.WARNING, logger="ghdag.workflow.dispatcher"):
            dispatcher._observe_rate_limit()

        audit_path = tmp_path / "audit.jsonl"
        assert not audit_path.exists()
        assert len(caplog.records) == 0

    def test_silent_continue_on_incomplete_rate_limit(self, tmp_path, caplog):
        """AC3 異常系2: get_rate_limit() が不完全な dict を返す場合、audit 書き込みなし・ログなし。"""
        dispatcher, github_client = _make_dispatcher(tmp_path)
        github_client.get_rate_limit.return_value = {"limit": 5000}  # remaining なし

        with caplog.at_level(logging.WARNING, logger="ghdag.workflow.dispatcher"):
            dispatcher._observe_rate_limit()

        audit_path = tmp_path / "audit.jsonl"
        assert not audit_path.exists()
        assert len(caplog.records) == 0

    def test_dispatch_proceeds_after_rate_limit_failure(self, tmp_path):
        """AC3: get_rate_limit() 失敗後も _observe_rate_limit() は例外を投げず dispatch は続行できる。"""
        dispatcher, github_client = _make_dispatcher(tmp_path)
        github_client.get_rate_limit.return_value = None

        # _observe_rate_limit() が例外を投げないこと
        dispatcher._observe_rate_limit()

        # dispatch も呼べること（mock が存在する）
        github_client.list_issues.return_value = []
        matches = dispatcher.poll_once()
        assert matches == []
