"""Tests for WorkflowDispatcher.poll_once per-trigger exception isolation.

ある workflow / trigger の list_issues 失敗が、他 trigger / workflow の評価を
巻き添えで停止させないこと（per-trigger exception isolation）を担保する。
"""

from __future__ import annotations

import logging
import subprocess
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
    pipeline = MagicMock(spec=LLMPipelineAPI)
    dispatcher = WorkflowDispatcher(
        workflows=workflows,
        github_client=github_client,
        pipeline=pipeline,
        queue_dir="queue",
    )
    return dispatcher, github_client


class TestPollOncePerTriggerIsolation:
    def test_failure_in_one_trigger_does_not_skip_subsequent_trigger(self):
        """ある trigger の list_issues 失敗が、後続 trigger の評価を止めないこと。"""
        wf = _make_workflow("wf", ["a:ready", "b:ready"])
        dispatcher, github_client = _make_dispatcher([wf])

        good_issue = _make_issue(99, "b:ready")

        def fake_list_issues(label: str):
            if label == "a:ready":
                raise subprocess.CalledProcessError(
                    returncode=1, cmd=["gh", "issue", "list", "--label", label]
                )
            return [good_issue]

        github_client.list_issues.side_effect = fake_list_issues

        results = dispatcher.poll_once()

        # 失敗した trigger は results に出ず、成功した trigger の Issue だけが含まれる
        assert len(results) == 1
        assert results[0]["issue"] == 99
        assert results[0]["_trigger"].label == "b:ready"

    def test_failure_in_one_workflow_does_not_skip_other_workflow(self):
        """ある workflow の trigger 失敗が、別 workflow の評価を止めないこと（本イシューの主目的）。"""
        wf_a = _make_workflow("inkwell", ["inkwell:draft-ready"])
        wf_b = _make_workflow("issuesmith", ["issuesmith:develop-ready"])
        dispatcher, github_client = _make_dispatcher([wf_a, wf_b])

        target_issue = _make_issue(612, "issuesmith:develop-ready")

        def fake_list_issues(label: str):
            if label == "inkwell:draft-ready":
                raise subprocess.CalledProcessError(
                    returncode=1, cmd=["gh", "issue", "list", "--label", label]
                )
            return [target_issue]

        github_client.list_issues.side_effect = fake_list_issues

        results = dispatcher.poll_once()

        # 後続 workflow (issuesmith) の Issue が拾える
        assert any(
            r["workflow"] == "issuesmith" and r["issue"] == 612 for r in results
        ), f"issuesmith#612 should be returned despite inkwell failure: {results}"

    def test_warning_logged_for_failed_trigger(self, caplog):
        """失敗した trigger について warning ログが出ること。"""
        wf = _make_workflow("wf", ["a:ready"])
        dispatcher, github_client = _make_dispatcher([wf])
        github_client.list_issues.side_effect = subprocess.CalledProcessError(
            returncode=1, cmd=["gh"]
        )

        with caplog.at_level(logging.WARNING, logger="ghdag.workflow.dispatcher"):
            dispatcher.poll_once()

        assert any(
            "a:ready" in r.message and r.levelno == logging.WARNING
            for r in caplog.records
        ), f"expected warning containing failed label: {[r.message for r in caplog.records]}"

    def test_no_exception_propagates_when_trigger_fails(self):
        """trigger 評価失敗が呼び出し元に例外として伝播しないこと。"""
        wf = _make_workflow("wf", ["a:ready", "b:ready"])
        dispatcher, github_client = _make_dispatcher([wf])
        github_client.list_issues.side_effect = RuntimeError("boom")

        # 例外が伝播せず、空 list が返る
        results = dispatcher.poll_once()
        assert results == []

    def test_all_triggers_succeed_unchanged_behavior(self):
        """全 trigger 成功時の挙動は従来と同じ（全 Issue が results に入る）。"""
        wf = _make_workflow("wf", ["a:ready", "b:ready"])
        dispatcher, github_client = _make_dispatcher([wf])

        github_client.list_issues.side_effect = lambda label: [
            _make_issue(1 if label == "a:ready" else 2, label)
        ]

        results = dispatcher.poll_once()

        assert {r["issue"] for r in results} == {1, 2}
