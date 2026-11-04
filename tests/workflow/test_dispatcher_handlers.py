"""Tests for ghdag.workflow — TC-1 to TC-11 (Issue #79 extended schema)."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# TC-1: YAML parse (happy path)
# ---------------------------------------------------------------------------
from ghdag_workflow_conftest_helpers import (
    _last_records,
    _make_dispatcher,
    _make_extended_workflow,
    _make_issue,
)

from ghdag.workflow.dispatcher import WorkflowDispatcher
from ghdag.workflow.schema import (
    HandlerConfig,
    StepConfig,
    TriggerConfig,
    WorkflowConfig,
)


class TestTC2MultiStepDag:
    def test_impl_handler_appends_three_exec_lines(self):
        workflow = _make_extended_workflow()
        dispatcher, _, pipeline_state, _ = _make_dispatcher(workflow)
        issue = _make_issue(42, ["pipeline:develop-ready"])
        handler = workflow.handlers["impl"]
        trigger = workflow.triggers[1]
        result = dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=1)

        assert result.status == "dispatched"
        records = _last_records(pipeline_state)
        # 3 step records (no idempotency line in JSONL - it's a field)
        assert len(records) == 3

    def test_impl_p2_has_depends_p1(self):
        workflow = _make_extended_workflow()
        dispatcher, _, pipeline_state, _ = _make_dispatcher(workflow)
        issue = _make_issue(42, ["pipeline:develop-ready"])
        handler = workflow.handlers["impl"]
        trigger = workflow.triggers[1]
        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=1)

        records = _last_records(pipeline_state)
        # records[0] = p1, records[1] = p2, records[2] = p3
        p1_rec = records[0]
        p2_rec = records[1]
        p3_rec = records[2]

        assert p2_rec["depends"] == [p1_rec["uuid"]]
        assert p3_rec["depends"] == [p2_rec["uuid"]]

    def test_brushup_single_step_no_depends(self):
        workflow = _make_extended_workflow()
        dispatcher, _, pipeline_state, _ = _make_dispatcher(workflow)
        issue = _make_issue(10, ["pipeline:draft-ready"])
        handler = workflow.handlers["brushup"]
        trigger = workflow.triggers[0]
        result = dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)

        assert result.status == "dispatched"
        records = _last_records(pipeline_state)
        assert len(records) == 1
        assert records[0]["depends"] == []


# ---------------------------------------------------------------------------
# TC-2b: context extension (result_filename, upstream result)
# ---------------------------------------------------------------------------


class TestTC2bContextExpansion:
    def test_context_contains_result_filename(self):
        """context includes ts, order_uuid, result_uuid, result_filename"""
        workflow = _make_extended_workflow()
        dispatcher, _, pipeline_state, order_builder = _make_dispatcher(workflow)
        issue = _make_issue(10, ["pipeline:draft-ready"])
        handler = workflow.handlers["brushup"]
        trigger = workflow.triggers[0]
        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)

        ctx = order_builder.contexts[-1]
        assert "ts" in ctx
        assert "order_uuid" in ctx
        assert "result_uuid" in ctx
        assert "result_filename" in ctx
        assert ctx["result_filename"] == f"{ctx['ts']}-claude-result-{ctx['result_uuid']}.md"

    def test_context_contains_dep_result_filename(self):
        """P2 context includes p1_result_filename"""
        workflow = _make_extended_workflow()
        dispatcher, _, pipeline_state, order_builder = _make_dispatcher(workflow)
        issue = _make_issue(42, ["pipeline:develop-ready"])
        handler = workflow.handlers["impl"]
        trigger = workflow.triggers[1]
        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=1)

        contexts = order_builder.contexts
        assert len(contexts) == 3

        p1_ctx = contexts[0]
        p2_ctx = contexts[1]
        p3_ctx = contexts[2]

        # p1 has no upstream dep, so no dep_result_filename
        assert not any(k.endswith("_result_filename") and k != "result_filename" for k in p1_ctx)

        # p2 has p1_result_filename
        assert "p1_result_filename" in p2_ctx
        expected_p1_result = f"{p1_ctx['ts']}-claude-result-{p1_ctx['result_uuid']}.md"
        assert p2_ctx["p1_result_filename"] == expected_p1_result

        # p3 has p2_result_filename
        assert "p2_result_filename" in p3_ctx
        expected_p2_result = f"{p2_ctx['ts']}-claude-result-{p2_ctx['result_uuid']}.md"
        assert p3_ctx["p2_result_filename"] == expected_p2_result

    def test_context_preserves_original_fields(self):
        """context still includes issue_number, workflow_name, handler_name"""
        workflow = _make_extended_workflow()
        dispatcher, _, _, order_builder = _make_dispatcher(workflow)
        issue = _make_issue(10, ["pipeline:draft-ready"])
        handler = workflow.handlers["brushup"]
        trigger = workflow.triggers[0]
        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)

        ctx = order_builder.contexts[-1]
        assert ctx["issue_number"] == "10"
        assert ctx["workflow_name"] == "stash-pipeline"
        assert ctx["handler_name"] == "brushup"


# ---------------------------------------------------------------------------
# TC-3: --model flag generation
# ---------------------------------------------------------------------------


class TestTC3ModelFlag:
    def test_model_in_exec_line(self):
        workflow = _make_extended_workflow()
        dispatcher, _, pipeline_state, _ = _make_dispatcher(workflow)
        issue = _make_issue(42, ["pipeline:develop-ready"])
        handler = workflow.handlers["impl"]
        trigger = workflow.triggers[1]
        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=1)

        records = _last_records(pipeline_state)
        p1_rec = records[0]
        assert "--model" in p1_rec["command"] and "claude-sonnet-4-6" in p1_rec["command"]

    def test_opus_model_in_brushup(self):
        workflow = _make_extended_workflow()
        dispatcher, _, pipeline_state, _ = _make_dispatcher(workflow)
        issue = _make_issue(10, ["pipeline:draft-ready"])
        handler = workflow.handlers["brushup"]
        trigger = workflow.triggers[0]
        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)

        records = _last_records(pipeline_state)
        assert "--model" in records[0]["command"] and "claude-opus-4-6" in records[0]["command"]


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

    def test_no_issue_context_no_design_md(self, tmp_path):
        workflow = _make_extended_workflow()
        dispatcher, github_client, pipeline_state, _ = _make_dispatcher(workflow, queue_dir=str(tmp_path))
        issue = _make_issue(42, ["pipeline:merge-ready"])
        handler = workflow.handlers["merge"]
        trigger = workflow.triggers[2]
        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=2)

        design_md = tmp_path / "issue-42-design.md"
        assert not design_md.exists()
        assert not github_client.comments_fetched


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

    def test_forward_transition_allowed(self):
        """develop-ready while draft-running → dispatched"""
        workflow = _make_extended_workflow()
        dispatcher, _, pipeline_state, _ = _make_dispatcher(workflow)
        # Issue has draft-running (rank 0)
        issue = _make_issue(42, ["pipeline:draft-running"])
        handler = workflow.handlers["impl"]
        trigger = workflow.triggers[1]  # develop-ready = rank 1
        result = dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=1)

        assert result.status == "dispatched"
        assert len(pipeline_state.appended) == 1

    def test_no_running_labels_allows_dispatch(self):
        """no running label → dispatched"""
        workflow = _make_extended_workflow()
        dispatcher, _, pipeline_state, _ = _make_dispatcher(workflow)
        issue = _make_issue(42, ["pipeline:draft-ready"])
        handler = workflow.handlers["brushup"]
        trigger = workflow.triggers[0]
        result = dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)

        assert result.status == "dispatched"


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

    def test_reset_calls_remove_idempotency(self):
        workflow = _make_extended_workflow()
        dispatcher, github_client, pipeline_state, _ = _make_dispatcher(workflow)
        issue = _make_issue(42, ["pipeline:develop-running"])
        handler = workflow.handlers["reset"]
        trigger = workflow.triggers[3]
        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=3)

        assert pipeline_state.remove_idempotency_calls == [("stash-pipeline", 42)]

    def test_reset_removes_pipeline_labels(self):
        workflow = _make_extended_workflow()
        dispatcher, github_client, pipeline_state, _ = _make_dispatcher(workflow)
        issue = _make_issue(42, ["pipeline:develop-running", "enhancement"])
        handler = workflow.handlers["reset"]
        trigger = workflow.triggers[3]
        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=3)

        # Only pipeline:* labels should be removed
        assert github_client.removed_labels == [(42, "pipeline:develop-running")]

    def test_reset_no_exec_lines_appended(self):
        workflow = _make_extended_workflow()
        dispatcher, github_client, pipeline_state, _ = _make_dispatcher(workflow)
        pipeline_state.remove_idempotency_return = 0
        issue = _make_issue(42, [])
        handler = workflow.handlers["reset"]
        trigger = workflow.triggers[3]
        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=3)

        assert not pipeline_state.appended

    def test_reset_removes_labels_with_custom_prefix(self):
        """Issue #12: reset should use trigger label prefix, not hardcoded 'pipeline:'."""
        workflow = WorkflowConfig(
            name="issuesmith",
            triggers=[
                TriggerConfig(label="issuesmith:draft-ready", handler="brushup"),
                TriggerConfig(label="issuesmith:reset", handler="reset"),
            ],
            handlers={
                "brushup": HandlerConfig(
                    steps=[StepConfig(template="brushup", model="claude-opus-4-6")],
                ),
                "reset": HandlerConfig(steps=[], type="reset"),
            },
        )
        dispatcher, github_client, pipeline_state, _ = _make_dispatcher(workflow)
        pipeline_state.remove_idempotency_return = 1
        issue = _make_issue(99, ["issuesmith:draft-running", "enhancement"])
        handler = workflow.handlers["reset"]
        trigger = workflow.triggers[1]  # issuesmith:reset
        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=1)

        assert github_client.removed_labels == [(99, "issuesmith:draft-running")]

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


class TestTC7Compatibility:

    def test_poll_once_returns_match(self):
        workflow = _make_extended_workflow()
        dispatcher, github_client, _, _ = _make_dispatcher(workflow)
        issue = _make_issue(1, ["pipeline:draft-ready"])
        github_client.list_all_issues_return = [issue]
        matches = dispatcher.poll_once()
        assert len(matches) >= 1
        assert matches[0]["issue"] == 1
        assert matches[0]["workflow"] == "stash-pipeline"
        assert matches[0]["handler"] == "brushup"

