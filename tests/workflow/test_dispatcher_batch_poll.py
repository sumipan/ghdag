"""Tests for WorkflowDispatcher batch poll via list_all_issues (nexus #3070).

Even with 32 triggers × 1 repository, list_all_issues is called once each for open/closed.
Label filtering is local. On bulk-fetch failure, skip all triggers for that github.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from ghdag.core.models.workflow import NonterminalClosedConfig
from ghdag.github_client import GitHubIssuePort
from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
from ghdag.workflow.dispatcher import WorkflowDispatcher
from ghdag.workflow.schema import (
    HandlerConfig,
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


def _make_workflow_with_n_triggers(n: int, *, with_nonterminal: bool = True) -> WorkflowConfig:
    triggers = [
        TriggerConfig(label=f"wf:t{i}-ready", handler="h") for i in range(n)
    ]
    nonterminal = None
    if with_nonterminal:
        nonterminal = NonterminalClosedConfig(
            action="reopen",
            terminal_labels=["wf:done"],
        )
    return WorkflowConfig(
        name="wf",
        triggers=triggers,
        handlers={
            "h": HandlerConfig(
                steps=[StepConfig(template="t", model="claude-opus-4-6")],
            ),
        },
        polling_interval=30,
        nonterminal_closed=nonterminal,
    )


def _make_dispatcher(workflow: WorkflowConfig) -> tuple[WorkflowDispatcher, MagicMock]:
    github = MagicMock(spec=GitHubIssuePort)
    github.list_all_issues.return_value = []
    github.get_issue_comments.return_value = []
    pipeline = MagicMock(spec=LLMPipelineAPI)
    dispatcher = WorkflowDispatcher(
        workflows=[workflow],
        github_client=github,
        pipeline=pipeline,
        queue_dir="queue",
    )
    return dispatcher, github


class TestBatchPollApiCalls:
    def test_32_triggers_one_repo_calls_list_all_issues_twice(self):
        """AC-1: with 32 triggers and 1 repo, list_all_issues is called only twice (open + closed)."""
        workflow = _make_workflow_with_n_triggers(32, with_nonterminal=True)
        dispatcher, github = _make_dispatcher(workflow)

        dispatcher.poll_once()

        assert github.list_all_issues.call_count == 2
        states = [c.args[0] if c.args else c.kwargs.get("state") for c in github.list_all_issues.call_args_list]
        assert states == ["open", "closed"]
        github.list_issues.assert_not_called()

    def test_without_nonterminal_only_open_once(self):
        """Without nonterminal_closed, only one open call."""
        workflow = _make_workflow_with_n_triggers(5, with_nonterminal=False)
        dispatcher, github = _make_dispatcher(workflow)

        dispatcher.poll_once()

        assert github.list_all_issues.call_count == 1
        assert github.list_all_issues.call_args.args[0] == "open"


class TestLocalLabelFilter:
    def test_filters_issues_by_trigger_label(self):
        """Locally filter bulk-fetch results by trigger.label."""
        workflow = _make_workflow_with_n_triggers(3, with_nonterminal=False)
        dispatcher, github = _make_dispatcher(workflow)
        issues = [
            _make_issue(1, ["wf:t0-ready"]),
            _make_issue(2, ["wf:t1-ready"]),
            _make_issue(3, ["other:label"]),
        ]
        github.list_all_issues.return_value = issues

        results = dispatcher.poll_once()

        assert {r["issue"] for r in results} == {1, 2}
        by_issue = {r["issue"]: r["_trigger"].label for r in results}
        assert by_issue[1] == "wf:t0-ready"
        assert by_issue[2] == "wf:t1-ready"


class TestBatchFetchIsolation:
    def test_batch_fetch_failure_skips_all_triggers(self, caplog):
        """On bulk-fetch failure, skip all triggers and emit a warning."""
        workflow = _make_workflow_with_n_triggers(3, with_nonterminal=False)
        dispatcher, github = _make_dispatcher(workflow)
        github.list_all_issues.side_effect = RuntimeError("api down")

        with caplog.at_level(logging.WARNING, logger="ghdag.workflow.dispatcher"):
            results = dispatcher.poll_once()

        assert results == []
        assert any("list_all_issues" in r.message or "api down" in r.message for r in caplog.records)

    def test_batch_success_does_not_call_list_issues(self):
        workflow = _make_workflow_with_n_triggers(2, with_nonterminal=False)
        dispatcher, github = _make_dispatcher(workflow)
        github.list_all_issues.return_value = [_make_issue(9, ["wf:t0-ready"])]

        results = dispatcher.poll_once()

        assert len(results) == 1
        assert results[0]["issue"] == 9
        github.list_issues.assert_not_called()
