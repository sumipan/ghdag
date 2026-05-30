"""Tests for WorkflowDispatcher error handling — TC-7, TC-9 (Issue #396)."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
from ghdag.workflow.dispatcher import WorkflowDispatcher
from ghdag.workflow.github import GitHubIssuePort
from ghdag.workflow.schema import (
    HandlerConfig,
    StepConfig,
    TriggerConfig,
    WorkflowConfig,
)


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


def _make_issue(number: int) -> dict:
    return {
        "number": number,
        "title": f"Issue {number}",
        "body": "body",
        "labels": [{"name": "pipeline:draft-ready"}],
        "url": f"https://github.com/owner/repo/issues/{number}",
    }


def _make_dispatcher(workflow: WorkflowConfig) -> tuple[WorkflowDispatcher, MagicMock]:
    github_client = MagicMock(spec=GitHubIssuePort)
    github_client.get_rate_limit.return_value = None  # rate limit 観測をスキップ
    pipeline = MagicMock(spec=LLMPipelineAPI)
    dispatcher = WorkflowDispatcher(
        workflows=[workflow],
        github_client=github_client,
        pipeline=pipeline,
        queue_dir="queue",
    )
    return dispatcher, github_client


class TestTC7DispatchErrorLogsOnly:
    def test_no_error_comment_on_dispatch_failure(self):
        """TC-7: dispatch() が例外を出してもIssueにエラーコメントは投稿しない"""
        workflow = _make_workflow()
        dispatcher, github_client = _make_dispatcher(workflow)
        issue = _make_issue(42)
        github_client.list_issues.return_value = [issue]

        dispatcher.dispatch = MagicMock(side_effect=KeyError("テンプレート展開エラー (brushup.md): 'missing'"))

        dispatcher.run(max_iterations=1)

        github_client.add_comment.assert_not_called()

    def test_error_log_includes_traceback(self, caplog):
        """TC-7: logger.exception が呼ばれる（スタックトレース付き）"""
        workflow = _make_workflow()
        dispatcher, github_client = _make_dispatcher(workflow)
        issue = _make_issue(42)
        github_client.list_issues.return_value = [issue]
        dispatcher.dispatch = MagicMock(side_effect=RuntimeError("boom"))

        with caplog.at_level(logging.ERROR, logger="ghdag.workflow.dispatcher"):
            dispatcher.run(max_iterations=1)

        assert any("dispatch failed" in r.message for r in caplog.records)
        assert any("issue #42" in r.message for r in caplog.records)

    def test_run_continues_after_dispatch_failure(self):
        """TC-7: dispatch() が例外を出しても run() はクラッシュしない"""
        workflow = _make_workflow()
        dispatcher, github_client = _make_dispatcher(workflow)
        issue = _make_issue(42)
        github_client.list_issues.return_value = [issue]
        dispatcher.dispatch = MagicMock(side_effect=RuntimeError("dispatch error"))

        dispatcher.run(max_iterations=1)


class TestTC9NonIntIssueNumberSkipsComment:
    def test_no_comment_when_issue_number_not_int(self):
        """TC-9: issue_number が int でない場合もクラッシュしない"""
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
