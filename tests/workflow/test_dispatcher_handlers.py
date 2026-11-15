"""Tests for ghdag.workflow — TC-1 to TC-11 (Issue #79 extended schema)."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# TC-1: YAML parse (happy path)
# ---------------------------------------------------------------------------
from ghdag_workflow_conftest_helpers import (
    _make_dispatcher,
    _make_extended_workflow,
    _make_issue,
)

from ghdag.core.models.workflow import (
    HandlerConfig,
    TriggerConfig,
    WorkflowConfig,
)
from ghdag.workflow.dispatcher import WorkflowDispatcher

# ---------------------------------------------------------------------------
# TC-4: Issue context fetch
# ---------------------------------------------------------------------------


class TestTC4IssueContext:
    def test_issue_context_true_writes_design_md(self, tmp_path):
        workflow = _make_extended_workflow()
        dispatcher, github_client, pipeline_state, _ = _make_dispatcher(workflow, queue_dir=str(tmp_path))
        # Restore real _write_design_md for this test
        dispatcher._write_design_md = WorkflowDispatcher._write_design_md.__get__(dispatcher, WorkflowDispatcher)
        github_client.comments = [
            {"author": "user1", "created_at": "2026-01-01T00:00:00Z", "body": "comment 1"},
            {"author": "user2", "created_at": "2026-01-02T00:00:00Z", "body": "comment 2"},
            {"author": "user3", "created_at": "2026-01-03T00:00:00Z", "body": "comment 3"},
        ]
        issue = _make_issue(79, ["pipeline:draft-ready"])
        handler = workflow.handlers["brushup"]
        trigger = workflow.triggers[0]
        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)

        design_md = tmp_path / "issue-79-design.md"
        assert design_md.exists()
        content = design_md.read_text(encoding="utf-8")
        assert "Issue body" in content
        assert "comment 1" in content
        assert "comment 2" in content
        assert "comment 3" in content


# ---------------------------------------------------------------------------
# TC-5: backward-transition guard
# ---------------------------------------------------------------------------


class TestTC5BackwardGuard:
    def test_backward_transition_blocked(self):
        """draft-ready while develop-running → skipped"""
        workflow = _make_extended_workflow()
        dispatcher, _, pipeline_state, _ = _make_dispatcher(workflow)
        # Issue has develop-running (rank 1)
        issue = _make_issue(42, ["pipeline:develop-running"])
        handler = workflow.handlers["brushup"]
        trigger = workflow.triggers[0]  # draft-ready = rank 0
        result = dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)

        assert result.status == "skipped"
        assert "backward" in result.reason
        assert not pipeline_state.appended


# ---------------------------------------------------------------------------
# TC-6: reset handler
# ---------------------------------------------------------------------------


class TestTC6ResetHandler:
    def test_reset_returns_reset_status(self):
        workflow = _make_extended_workflow()
        dispatcher, github_client, pipeline_state, _ = _make_dispatcher(workflow)
        issue = _make_issue(42, ["pipeline:develop-running"])
        handler = workflow.handlers["reset"]
        trigger = workflow.triggers[3]  # pipeline:reset (rank 3)
        result = dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=3)

        assert result.status == "reset"

    def test_reset_uses_label_namespace_when_set(self):
        """When label_namespace is set, use it as prefix instead of trigger.label."""
        workflow = WorkflowConfig(
            name="custom-wf",
            label_namespace="custom",
            triggers=[
                TriggerConfig(label="other:reset", handler="reset"),
            ],
            handlers={
                "reset": HandlerConfig(steps=[], type="reset"),
            },
        )
        dispatcher, github_client, pipeline_state, _ = _make_dispatcher(workflow)
        pipeline_state.remove_idempotency_return = 1
        issue = _make_issue(
            10,
            ["custom:draft-running", "custom:impl-running", "other:reset"],
        )
        handler = workflow.handlers["reset"]
        trigger = workflow.triggers[0]
        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)

        removed = {label for _issue, label in github_client.removed_labels}
        assert removed == {"custom:draft-running", "custom:impl-running"}
        assert "other:reset" not in removed
