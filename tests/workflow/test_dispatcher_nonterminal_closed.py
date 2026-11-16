"""Tests for nonterminal_closed detection in WorkflowDispatcher — Issue #2870.

Updated for nexus #3070: poll uses list_all_issues batch fetch + local label filter.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ghdag.core.models.workflow import (
    HandlerConfig,
    NonterminalClosedConfig,
    StepConfig,
    TriggerConfig,
    WorkflowConfig,
)
from ghdag.github_client import GitHubClient, GitHubIssuePort
from ghdag.pipeline.task_queue import TaskQueue
from ghdag.workflow.dispatcher import WorkflowDispatcher
from ghdag.workflow.loader import _parse


def _make_issue(number: int, labels: list[str], *, state: str = "open") -> dict:
    return {
        "number": number,
        "title": f"Issue {number}",
        "body": "body",
        "state": state,
        "labels": [{"name": lb} for lb in labels],
        "url": f"https://github.com/owner/repo/issues/{number}",
    }


def _make_workflow(
    *,
    triggers: list[str] | None = None,
    nonterminal_closed: NonterminalClosedConfig | None = None,
    handler_name: str = "impl",
) -> WorkflowConfig:
    trigger_labels = triggers or ["issuesmith:develop-done"]
    return WorkflowConfig(
        name="issuesmith",
        triggers=[
            TriggerConfig(label=lb, handler=handler_name if lb.endswith("done") else "merge")
            for lb in trigger_labels
        ]
        if len(trigger_labels) > 1
        else [TriggerConfig(label=trigger_labels[0], handler=handler_name)],
        handlers={
            handler_name: HandlerConfig(
                steps=[StepConfig(template="impl", model="claude-opus-4-6")],
            ),
            "merge": HandlerConfig(
                steps=[StepConfig(template="merge", model="claude-opus-4-6")],
            ),
        },
        polling_interval=30,
        nonterminal_closed=nonterminal_closed,
    )


def _make_dispatcher(workflow: WorkflowConfig):
    github_client = MagicMock(spec=GitHubIssuePort)
    github_client.list_all_issues.return_value = []
    github_client.get_last_rate_limit.return_value = None
    github_client.get_issue_comments.return_value = []
    order_builder = MagicMock()
    order_builder.build_order.return_value = "order content"
    task_queue = MagicMock(spec=TaskQueue)
    task_queue.check_idempotency.return_value = True
    task_queue.get_generation.return_value = 0
    task_queue.order_builder_for.return_value = order_builder
    task_queue.enqueue.return_value = ['{"uuid": "u"}']
    dispatcher = WorkflowDispatcher(
        workflows=[workflow],
        github_client=github_client,
        task_queue=task_queue,
        queue_dir="queue",
    )
    dispatcher._write_design_md = MagicMock()
    return dispatcher, github_client, task_queue


def _fake_list_all(open_issues=None, closed_issues=None):
    open_issues = open_issues or []
    closed_issues = closed_issues or []

    def _impl(state: str = "open"):
        if state == "closed":
            return list(closed_issues)
        return list(open_issues)

    return _impl


class TestReopenAction:
    def test_closed_nonterminal_issue_is_reopened(self):
        config = NonterminalClosedConfig(
            action="reopen",
            terminal_labels=["issuesmith:merge-done", "issuesmith:rejected"],
        )
        workflow = _make_workflow(nonterminal_closed=config)
        dispatcher, github_client, _ = _make_dispatcher(workflow)
        issue = _make_issue(42, ["issuesmith:develop-done"], state="closed")
        github_client.list_all_issues.side_effect = _fake_list_all(
            closed_issues=[issue],
        )

        results = dispatcher.poll_once()

        github_client.reopen_issue.assert_called_once_with(42)
        github_client.add_comment.assert_called_once()
        assert "<!-- ghdag:nonterminal_closed:reopen:42 -->" in github_client.add_comment.call_args[0][1]
        assert results == []


class TestIdempotency:
    def test_marker_prevents_duplicate_reopen(self):
        config = NonterminalClosedConfig(
            action="reopen",
            terminal_labels=["issuesmith:merge-done"],
        )
        workflow = _make_workflow(nonterminal_closed=config)
        dispatcher, github_client, _ = _make_dispatcher(workflow)
        issue = _make_issue(55, ["issuesmith:develop-done"], state="closed")
        github_client.list_all_issues.side_effect = _fake_list_all(closed_issues=[issue])
        github_client.get_issue_comments.return_value = [
            {
                "author": "bot",
                "created_at": "2026-01-01T00:00:00Z",
                "body": "<!-- ghdag:nonterminal_closed:reopen:55 -->",
            }
        ]

        dispatcher.poll_once()
        dispatcher.poll_once()

        github_client.reopen_issue.assert_not_called()


class TestTriggerAction:
    def test_trigger_action_dispatches_handler_while_issue_stays_closed(self):
        config = NonterminalClosedConfig(
            action="trigger",
            trigger="issuesmith:merge-ready",
            terminal_labels=["issuesmith:merge-done"],
        )
        workflow = _make_workflow(
            triggers=["issuesmith:develop-done", "issuesmith:merge-ready"],
            nonterminal_closed=config,
        )
        dispatcher, github_client, _ = _make_dispatcher(workflow)
        issue = _make_issue(88, ["issuesmith:develop-done"], state="closed")
        github_client.list_all_issues.side_effect = _fake_list_all(closed_issues=[issue])

        matches = dispatcher.poll_once()

        assert len(matches) == 1
        assert matches[0]["issue"] == 88
        assert matches[0]["handler"] == "merge"
        assert matches[0]["_trigger"].label == "issuesmith:merge-ready"
        github_client.reopen_issue.assert_not_called()
        github_client.add_comment.assert_called_once()
        assert "<!-- ghdag:nonterminal_closed:trigger:88 -->" in github_client.add_comment.call_args[0][1]

        result = dispatcher.dispatch(
            matches[0]["_issue_data"],
            matches[0]["_workflow"],
            matches[0]["_handler"],
            trigger=matches[0]["_trigger"],
            trigger_rank=matches[0]["_trigger_rank"],
            github=github_client,
        )
        assert result.status == "dispatched"


class TestLoaderParse:
    def test_parse_nonterminal_closed(self):
        data = {
            "name": "issuesmith",
            "triggers": [{"label": "issuesmith:develop-done", "handler": "impl"}],
            "handlers": {
                "impl": {"steps": [{"template": "impl", "model": "claude-opus-4-6"}]},
            },
            "nonterminal_closed": {
                "action": "reopen",
                "terminal_labels": ["issuesmith:merge-done"],
            },
        }
        config = _parse(data)
        assert config.nonterminal_closed is not None
        assert config.nonterminal_closed.action == "reopen"
        assert config.nonterminal_closed.terminal_labels == ["issuesmith:merge-done"]
        assert config.nonterminal_closed.trigger is None


class TestReopenIssue:
    def test_github_client_reopen_issue(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = GitHubClient(token="token", repo="owner/repo")
        captured: dict[str, object] = {}

        def fake_request(method, path, **kwargs):
            captured["method"] = method
            captured["path"] = path
            captured["body"] = kwargs.get("body")
            return {}

        monkeypatch.setattr(client, "_request", fake_request)
        client.reopen_issue(123)
        assert captured["method"] == "PATCH"
        assert captured["path"].endswith("/issues/123")
        assert captured["body"] == {"state": "open"}

    def test_github_client_satisfies_reopen_issue_port(self) -> None:
        client = GitHubClient(token="token", repo="owner/repo")
        assert hasattr(client, "reopen_issue")
        assert isinstance(client, GitHubIssuePort)
