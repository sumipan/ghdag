"""Tests for nonterminal_closed detection in WorkflowDispatcher — Issue #2870."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ghdag.github_client import GitHubClient, GitHubIssuePort
from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
from ghdag.workflow.dispatcher import WorkflowDispatcher
from ghdag.workflow.loader import _parse
from ghdag.workflow.schema import (
    HandlerConfig,
    NonterminalClosedConfig,
    StepConfig,
    TriggerConfig,
    WorkflowConfig,
)


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
    github_client.list_issues.return_value = []
    github_client.get_issue_comments.return_value = []
    pipeline_state = MagicMock()
    pipeline_state.check_idempotency.return_value = True
    pipeline_state.get_generation.return_value = 0
    pipeline_state.write_order_file.return_value = "ts-claude-order-uuid.md"
    order_builder = MagicMock()
    order_builder.build_order.return_value = "order content"
    pipeline = LLMPipelineAPI(
        pipeline_state=pipeline_state,
        order_builder=order_builder,
        queue_dir="queue",
    )
    dispatcher = WorkflowDispatcher(
        workflows=[workflow],
        github_client=github_client,
        pipeline=pipeline,
        queue_dir="queue",
    )
    dispatcher._write_design_md = MagicMock()
    return dispatcher, github_client, pipeline_state


class TestBackwardCompatibility:
    def test_poll_once_without_nonterminal_closed_uses_open_only(self):
        workflow = _make_workflow()
        dispatcher, github_client, _ = _make_dispatcher(workflow)

        dispatcher.poll_once()

        for call in github_client.list_issues.call_args_list:
            assert call.args[1:] == () or call.kwargs.get("state", "open") == "open"
            if call.args:
                assert len(call.args) == 1 or call.args[1] == "open"

    def test_poll_once_without_nonterminal_closed_no_closed_scan(self):
        workflow = _make_workflow()
        dispatcher, github_client, _ = _make_dispatcher(workflow)

        dispatcher.poll_once()

        assert all(
            (call.kwargs.get("state") or (call.args[1] if len(call.args) > 1 else "open")) != "closed"
            for call in github_client.list_issues.call_args_list
        )


class TestReopenAction:
    def test_closed_nonterminal_issue_is_reopened(self):
        config = NonterminalClosedConfig(
            action="reopen",
            terminal_labels=["issuesmith:merge-done", "issuesmith:rejected"],
        )
        workflow = _make_workflow(nonterminal_closed=config)
        dispatcher, github_client, _ = _make_dispatcher(workflow)
        issue = _make_issue(42, ["issuesmith:develop-done"], state="closed")

        def fake_list_issues(label: str, state: str = "open"):
            if state == "closed" and label == "issuesmith:develop-done":
                return [issue]
            return []

        github_client.list_issues.side_effect = fake_list_issues

        results = dispatcher.poll_once()

        github_client.reopen_issue.assert_called_once_with(42)
        github_client.add_comment.assert_called_once()
        assert "<!-- ghdag:nonterminal_closed:reopen:42 -->" in github_client.add_comment.call_args[0][1]
        assert results == []

    def test_terminal_closed_issue_is_not_reopened(self):
        config = NonterminalClosedConfig(
            action="reopen",
            terminal_labels=["issuesmith:merge-done", "issuesmith:rejected"],
        )
        workflow = _make_workflow(nonterminal_closed=config)
        dispatcher, github_client, _ = _make_dispatcher(workflow)
        issue = _make_issue(99, ["issuesmith:merge-done"], state="closed")

        def fake_list_issues(label: str, state: str = "open"):
            if state == "closed":
                return [issue]
            return []

        github_client.list_issues.side_effect = fake_list_issues

        dispatcher.poll_once()

        github_client.reopen_issue.assert_not_called()
        github_client.add_comment.assert_not_called()

    def test_reopened_issue_detected_on_next_open_scan(self):
        config = NonterminalClosedConfig(
            action="reopen",
            terminal_labels=["issuesmith:merge-done"],
        )
        workflow = _make_workflow(nonterminal_closed=config)
        dispatcher, github_client, _ = _make_dispatcher(workflow)
        closed_issue = _make_issue(7, ["issuesmith:develop-done"], state="closed")
        open_issue = _make_issue(7, ["issuesmith:develop-done"], state="open")
        phase = {"reopened": False}

        def fake_list_issues(label: str, state: str = "open"):
            if state == "closed" and not phase["reopened"]:
                return [closed_issue]
            if state == "open":
                return [open_issue] if phase["reopened"] else []
            return []

        def fake_reopen(number: int):
            phase["reopened"] = True

        github_client.list_issues.side_effect = fake_list_issues
        github_client.reopen_issue.side_effect = fake_reopen

        dispatcher.poll_once()
        matches = dispatcher.poll_once()

        assert github_client.reopen_issue.call_count == 1
        assert len(matches) == 1
        assert matches[0]["issue"] == 7


class TestIdempotency:
    def test_marker_prevents_duplicate_reopen(self):
        config = NonterminalClosedConfig(
            action="reopen",
            terminal_labels=["issuesmith:merge-done"],
        )
        workflow = _make_workflow(nonterminal_closed=config)
        dispatcher, github_client, _ = _make_dispatcher(workflow)
        issue = _make_issue(55, ["issuesmith:develop-done"], state="closed")
        github_client.list_issues.side_effect = lambda label, state="open": (
            [issue] if state == "closed" else []
        )
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

        def fake_list_issues(label: str, state: str = "open"):
            if state == "closed" and label == "issuesmith:develop-done":
                return [issue]
            return []

        github_client.list_issues.side_effect = fake_list_issues

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

    def test_parse_without_nonterminal_closed_returns_none(self):
        data = {
            "name": "issuesmith",
            "triggers": [{"label": "issuesmith:develop-done", "handler": "impl"}],
            "handlers": {
                "impl": {"steps": [{"template": "impl", "model": "claude-opus-4-6"}]},
            },
        }
        config = _parse(data)
        assert config.nonterminal_closed is None


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
