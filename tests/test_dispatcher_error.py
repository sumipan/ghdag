"""Tests for WorkflowDispatcher error handling — TC-7, TC-8, TC-9 (Issue #396)."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
from ghdag.workflow.dispatcher import WorkflowDispatcher
from ghdag.workflow.github import GitHubIssueClient
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
    github_client = MagicMock(spec=GitHubIssueClient)
    pipeline = MagicMock(spec=LLMPipelineAPI)
    dispatcher = WorkflowDispatcher(
        workflows=[workflow],
        github_client=github_client,
        pipeline=pipeline,
        queue_dir="queue",
    )
    return dispatcher, github_client


class TestTC7DispatchErrorPostsComment:
    def test_error_comment_posted_on_dispatch_failure(self):
        """TC-7: dispatch() が例外を出したとき Issue にエラーコメントが投稿される"""
        workflow = _make_workflow()
        dispatcher, github_client = _make_dispatcher(workflow)
        issue = _make_issue(42)
        github_client.list_issues.return_value = [issue]

        # dispatch を失敗させる
        dispatcher.dispatch = MagicMock(side_effect=KeyError("テンプレート展開エラー (brushup.md): 'missing'"))

        dispatcher.run(max_iterations=1)

        github_client.post_comment.assert_called_once()
        args = github_client.post_comment.call_args
        assert args[0][0] == 42  # issue_number
        body = args[0][1]
        assert "brushup" in body  # handler name

    def test_error_comment_contains_traceback(self):
        """TC-7: エラーコメントにスタックトレースが含まれる"""
        workflow = _make_workflow()
        dispatcher, github_client = _make_dispatcher(workflow)
        issue = _make_issue(42)
        github_client.list_issues.return_value = [issue]
        dispatcher.dispatch = MagicMock(side_effect=KeyError("missing_key"))

        dispatcher.run(max_iterations=1)

        body = github_client.post_comment.call_args[0][1]
        assert "KeyError" in body or "missing_key" in body

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


class TestTC8CommentPostFailureDoesNotCrash:
    def test_run_continues_if_post_comment_fails(self):
        """TC-8: コメント投稿が失敗しても run() はクラッシュしない"""
        workflow = _make_workflow()
        dispatcher, github_client = _make_dispatcher(workflow)
        issue = _make_issue(42)
        github_client.list_issues.return_value = [issue]
        dispatcher.dispatch = MagicMock(side_effect=RuntimeError("dispatch error"))
        github_client.post_comment.side_effect = RuntimeError("comment error")

        # クラッシュしないこと
        dispatcher.run(max_iterations=1)

    def test_warning_logged_if_post_comment_fails(self, caplog):
        """TC-8: コメント投稿失敗時に warning ログが出る"""
        workflow = _make_workflow()
        dispatcher, github_client = _make_dispatcher(workflow)
        issue = _make_issue(42)
        github_client.list_issues.return_value = [issue]
        dispatcher.dispatch = MagicMock(side_effect=RuntimeError("dispatch error"))
        github_client.post_comment.side_effect = RuntimeError("comment error")

        with caplog.at_level(logging.WARNING, logger="ghdag.workflow.dispatcher"):
            dispatcher.run(max_iterations=1)

        assert any(
            "post error comment" in r.message.lower() or "Failed to post" in r.message
            for r in caplog.records
        )


class TestTC9NonIntIssueNumberSkipsComment:
    def test_no_comment_when_issue_number_not_int(self):
        """TC-9: issue_number が int でない場合はコメント投稿をスキップ"""
        workflow = _make_workflow()
        dispatcher, github_client = _make_dispatcher(workflow)

        # _issue_data の number が str の壊れたデータ（poll_once が返す match を直接差し込む）
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

        # クラッシュしない
        dispatcher.run(max_iterations=1)

        # post_comment は呼ばれない
        github_client.post_comment.assert_not_called()
