"""Tests for WorkflowDispatcher multi-repo support (GITHUB_REPOSITORIES).

複数の GitHub クライアント（リポジトリごと）を渡したとき、poll_once が各リポを
横断して Issue を集め、dispatch が「その Issue を取得したクライアント」に対して
ラベル遷移・コメント等を行うこと（per-repo routing）を担保する。
"""

from __future__ import annotations

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


def _make_workflow() -> WorkflowConfig:
    return WorkflowConfig(
        name="wf",
        triggers=[TriggerConfig(label="wf:draft-ready", handler="h")],
        handlers={
            "h": HandlerConfig(
                steps=[StepConfig(template="t", model="claude-opus-4-6")],
            ),
        },
        polling_interval=0,
    )


def _make_issue(number: int) -> dict:
    return {
        "number": number,
        "title": f"Issue {number}",
        "body": "body",
        "labels": [{"name": "wf:draft-ready"}],
        "url": f"https://github.com/owner/repo/issues/{number}",
    }


def _make_client(issues: list[dict]) -> MagicMock:
    client = MagicMock(spec=GitHubIssuePort)
    client.list_issues.return_value = issues
    client.get_rate_limit.return_value = None
    return client


def test_poll_once_collects_issues_from_all_clients():
    """poll_once は各クライアントの Issue を集め、由来クライアントを _github に付与する。"""
    issue_a = _make_issue(1)
    issue_b = _make_issue(2)
    client_a = _make_client([issue_a])
    client_b = _make_client([issue_b])
    pipeline = MagicMock(spec=LLMPipelineAPI)
    dispatcher = WorkflowDispatcher(
        workflows=[_make_workflow()],
        github_client=[client_a, client_b],
        pipeline=pipeline,
        queue_dir="queue",
    )

    results = dispatcher.poll_once()

    assert {r["issue"] for r in results} == {1, 2}
    by_issue = {r["issue"]: r["_github"] for r in results}
    assert by_issue[1] is client_a
    assert by_issue[2] is client_b


def test_dispatch_label_transition_routed_to_originating_client():
    """dispatch の *-ready→*-running ラベル遷移は、その Issue を取得したクライアントに対して行う。"""
    issue_a = _make_issue(1)
    issue_b = _make_issue(2)
    client_a = _make_client([issue_a])
    client_b = _make_client([issue_b])
    pipeline = MagicMock(spec=LLMPipelineAPI)
    pipeline.check_idempotency.return_value = True
    pipeline.submit.return_value = ["uuid: x"]
    dispatcher = WorkflowDispatcher(
        workflows=[_make_workflow()],
        github_client=[client_a, client_b],
        pipeline=pipeline,
        queue_dir="queue",
    )

    dispatcher.run(max_iterations=1)

    # それぞれのリポの Issue 番号に対し、対応するクライアントだけが update_label される
    client_a.update_label.assert_called_once_with(1, "wf:draft-ready", "wf:draft-running")
    client_b.update_label.assert_called_once_with(2, "wf:draft-ready", "wf:draft-running")


def test_single_client_still_accepted():
    """後方互換: 単一クライアントを渡しても従来通り動く。"""
    issue = _make_issue(7)
    client = _make_client([issue])
    pipeline = MagicMock(spec=LLMPipelineAPI)
    dispatcher = WorkflowDispatcher(
        workflows=[_make_workflow()],
        github_client=client,
        pipeline=pipeline,
        queue_dir="queue",
    )

    results = dispatcher.poll_once()
    assert [r["issue"] for r in results] == [7]
    assert results[0]["_github"] is client
