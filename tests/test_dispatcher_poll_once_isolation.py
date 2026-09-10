"""Tests for WorkflowDispatcher.poll_once exception isolation after batch fetch.

一括取得 (list_all_issues) 失敗時は当該クライアントの全 trigger をスキップする。
ラベルフィルタ中の例外は trigger 単位でスキップする（per-trigger isolation）。
"""

from __future__ import annotations

import logging
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
    pipeline = MagicMock(spec=LLMPipelineAPI)
    dispatcher = WorkflowDispatcher(
        workflows=workflows,
        github_client=github_client,
        pipeline=pipeline,
        queue_dir="queue",
    )
    return dispatcher, github_client


class TestPollOncePerTriggerIsolation:
    def test_batch_failure_skips_all_triggers(self, caplog):
        """一括取得失敗時は全 trigger をスキップし warning。"""
        wf = _make_workflow("wf", ["a:ready", "b:ready"])
        dispatcher, github_client = _make_dispatcher([wf])
        github_client.list_all_issues.side_effect = RuntimeError("api down")

        with caplog.at_level(logging.WARNING, logger="ghdag.workflow.dispatcher"):
            results = dispatcher.poll_once()

        assert results == []
        assert any("list_all_issues" in r.message for r in caplog.records)

    def test_filter_failure_in_one_issue_does_not_skip_subsequent(self):
        """ラベルフィルタ中の個別 issue 例外が他 issue / trigger を止めないこと。"""
        wf = _make_workflow("wf", ["a:ready", "b:ready"])
        dispatcher, github_client = _make_dispatcher([wf])

        bad = {"number": 1, "labels": "not-a-list"}  # labels が iterable of dict でない
        good_a = _make_issue(10, "a:ready")
        good_b = _make_issue(99, "b:ready")
        github_client.list_all_issues.return_value = [bad, good_a, good_b]

        results = dispatcher.poll_once()

        assert {r["issue"] for r in results} == {10, 99}

    def test_warning_logged_for_failed_batch(self, caplog):
        """一括取得失敗について warning ログが出ること。"""
        wf = _make_workflow("wf", ["a:ready"])
        dispatcher, github_client = _make_dispatcher([wf])
        github_client.list_all_issues.side_effect = RuntimeError("boom")

        with caplog.at_level(logging.WARNING, logger="ghdag.workflow.dispatcher"):
            dispatcher.poll_once()

        assert any(
            "a:ready" not in r.message and r.levelno == logging.WARNING
            for r in caplog.records
        ) or any("list_all_issues" in r.message for r in caplog.records)

    def test_no_exception_propagates_when_batch_fails(self):
        """一括取得失敗が呼び出し元に例外として伝播しないこと。"""
        wf = _make_workflow("wf", ["a:ready", "b:ready"])
        dispatcher, github_client = _make_dispatcher([wf])
        github_client.list_all_issues.side_effect = RuntimeError("boom")

        results = dispatcher.poll_once()
        assert results == []

    def test_all_triggers_succeed_unchanged_behavior(self):
        """全 trigger 成功時はマッチした Issue がすべて results に入る。"""
        wf = _make_workflow("wf", ["a:ready", "b:ready"])
        dispatcher, github_client = _make_dispatcher([wf])

        github_client.list_all_issues.return_value = [
            _make_issue(1, "a:ready"),
            _make_issue(2, "b:ready"),
        ]

        results = dispatcher.poll_once()

        assert {r["issue"] for r in results} == {1, 2}
