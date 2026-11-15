"""Tests for ghdag.workflow — TC-1 to TC-11 (Issue #79 extended schema)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from ghdag_workflow_conftest_helpers import _make_dispatcher, _make_extended_workflow, _make_issue

from ghdag.core.models.workflow import HandlerConfig, StepConfig, TriggerConfig, WorkflowConfig
from ghdag.workflow.dispatcher import ContextHookError


class TestTC9ContextHook:
    def test_context_hook_merges_into_template_context(self):
        """context_hook output is merged into template context"""
        workflow = WorkflowConfig(
            name="test",
            triggers=[TriggerConfig(label="pipeline:draft-ready", handler="brushup")],
            handlers={
                "brushup": HandlerConfig(steps=[StepConfig(template="brushup", model="opus")], context_hook="echo hook")
            },
        )
        dispatcher, _, pipeline_state, order_builder = _make_dispatcher(workflow)
        hook_calls: list[tuple[str, int]] = []

        def fake_hook(cmd, issue_number):
            hook_calls.append((cmd, issue_number))
            return {"pipeline_id": "test-123", "worktree_path": "/tmp/wt"}

        dispatcher._run_context_hook = fake_hook
        issue = _make_issue(10, ["pipeline:draft-ready"])
        handler = workflow.handlers["brushup"]
        trigger = workflow.triggers[0]
        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)
        assert hook_calls == [("echo hook", 10)]
        ctx = order_builder.contexts[-1]
        assert ctx["pipeline_id"] == "test-123"
        assert ctx["worktree_path"] == "/tmp/wt"
        assert ctx["issue_number"] == "10"

    def test_run_context_hook_parses_json(self):
        """_run_context_hook parses stdout JSON and returns a dict"""
        workflow = _make_extended_workflow()
        dispatcher, _, _, _ = _make_dispatcher(workflow)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"key": "value", "num": 42}'
        with patch("ghdag.workflow.dispatcher.subprocess.run", return_value=mock_result):
            result = dispatcher._run_context_hook("echo test", 10)
        assert result == {"key": "value", "num": "42"}

    def test_run_context_hook_raises_on_invalid_json(self):
        """_run_context_hook raises ValueError on invalid JSON"""
        workflow = _make_extended_workflow()
        dispatcher, _, _, _ = _make_dispatcher(workflow)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "not json"
        with patch("ghdag.workflow.dispatcher.subprocess.run", return_value=mock_result):
            with pytest.raises(ContextHookError, match="JSON"):
                dispatcher._run_context_hook("echo test", 10)

    def test_run_context_hook_returns_empty_on_whitespace_stdout(self):
        """_run_context_hook returns empty dict when stdout is whitespace only"""
        workflow = _make_extended_workflow()
        dispatcher, _, _, _ = _make_dispatcher(workflow)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "   \n\t  "
        with patch("ghdag.workflow.dispatcher.subprocess.run", return_value=mock_result):
            result = dispatcher._run_context_hook("echo test", 10)
        assert result == {}

    def test_run_context_hook_propagates_timeout(self):
        """_run_context_hook propagates subprocess.TimeoutExpired"""
        import subprocess

        workflow = _make_extended_workflow()
        dispatcher, _, _, _ = _make_dispatcher(workflow)
        with patch(
            "ghdag.workflow.dispatcher.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="hook", timeout=30)
        ):
            with pytest.raises(subprocess.TimeoutExpired):
                dispatcher._run_context_hook("slow-cmd", 10)

    def test_dispatch_aborts_when_context_hook_fails(self):
        """dispatch does not enqueue or transition labels when context_hook fails"""
        workflow = WorkflowConfig(
            name="test",
            triggers=[TriggerConfig(label="pipeline:draft-ready", handler="brushup")],
            handlers={
                "brushup": HandlerConfig(steps=[StepConfig(template="brushup", model="opus")], context_hook="fail-hook")
            },
        )
        dispatcher, github_client, _, order_builder = _make_dispatcher(workflow)
        submit_calls: list = []
        dispatcher._submit_steps = lambda *args, **kwargs: submit_calls.append((args, kwargs))
        mock_result = MagicMock()
        mock_result.returncode = 7
        mock_result.stderr = "hook failed"
        with patch("ghdag.workflow.dispatcher.subprocess.run", return_value=mock_result):
            issue = _make_issue(10, ["pipeline:draft-ready"])
            handler = workflow.handlers["brushup"]
            trigger = workflow.triggers[0]
            with pytest.raises(ContextHookError):
                dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)
        assert not submit_calls
        assert not github_client.updated_labels
        assert order_builder.build_calls == 0
