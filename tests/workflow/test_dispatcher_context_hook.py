"""Tests for ghdag.workflow — TC-1 to TC-11 (Issue #79 extended schema)."""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, patch

import pytest

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
    StepConfig,
    TriggerConfig,
    WorkflowConfig,
)
from ghdag.workflow.dispatcher import ContextHookError


class TestTC9ContextHook:

    def test_context_hook_merges_into_template_context(self):
        """context_hook output is merged into template context"""
        workflow = WorkflowConfig(
            name="test",
            triggers=[TriggerConfig(label="pipeline:draft-ready", handler="brushup")],
            handlers={
                "brushup": HandlerConfig(
                    steps=[StepConfig(template="brushup", model="opus")],
                    context_hook="echo hook",
                ),
            },
        )
        dispatcher, _, pipeline_state, order_builder = _make_dispatcher(workflow)
        hook_calls: list[tuple[str, int]] = []

        def fake_hook(cmd, issue_number):
            hook_calls.append((cmd, issue_number))
            return {
                "pipeline_id": "test-123",
                "worktree_path": "/tmp/wt",
            }

        dispatcher._run_context_hook = fake_hook
        issue = _make_issue(10, ["pipeline:draft-ready"])
        handler = workflow.handlers["brushup"]
        trigger = workflow.triggers[0]
        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)

        assert hook_calls == [("echo hook", 10)]
        ctx = order_builder.contexts[-1]
        assert ctx["pipeline_id"] == "test-123"
        assert ctx["worktree_path"] == "/tmp/wt"
        # base context is still present
        assert ctx["issue_number"] == "10"

    def test_context_hook_not_called_when_none(self):
        """_run_context_hook is not called when context_hook is None"""
        workflow = _make_extended_workflow()
        dispatcher, _, _, _ = _make_dispatcher(workflow)
        hook_ran = False

        def noop_hook(*_args, **_kwargs):
            nonlocal hook_ran
            hook_ran = True
            return {}

        dispatcher._run_context_hook = noop_hook
        issue = _make_issue(10, ["pipeline:draft-ready"])
        handler = workflow.handlers["brushup"]
        trigger = workflow.triggers[0]
        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)

        assert not hook_ran

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

    def test_run_context_hook_raises_on_nonzero_exit(self):
        """_run_context_hook raises ContextHookError on non-zero exit"""
        workflow = _make_extended_workflow()
        dispatcher, _, _, _ = _make_dispatcher(workflow)

        mock_result = MagicMock()
        mock_result.returncode = 7
        mock_result.stderr = "  hook failed  "
        with patch("ghdag.workflow.dispatcher.subprocess.run", return_value=mock_result):
            with pytest.raises(ContextHookError, match=r"rc=7") as exc_info:
                dispatcher._run_context_hook("bad-cmd", 10)

        assert "hook failed" in str(exc_info.value)

    def test_run_context_hook_stderr_tail_truncated(self, caplog):
        """stderr in exception and log is limited to last 500 chars after strip"""
        workflow = _make_extended_workflow()
        dispatcher, _, _, _ = _make_dispatcher(workflow)

        prefix = "X" * 600
        suffix = "Y" * 400
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = prefix + suffix
        with patch("ghdag.workflow.dispatcher.subprocess.run", return_value=mock_result):
            with caplog.at_level(logging.WARNING, logger="ghdag.workflow.dispatcher"):
                with pytest.raises(ContextHookError) as exc_info:
                    dispatcher._run_context_hook("bad-cmd", 10)

        msg = str(exc_info.value)
        assert prefix not in msg
        assert suffix in msg
        warning_msgs = [r.message for r in caplog.records if "context_hook failed" in r.message]
        assert len(warning_msgs) == 1
        assert prefix not in warning_msgs[0]
        assert suffix in warning_msgs[0]

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

    def test_run_context_hook_returns_empty_on_empty_stdout(self):
        """_run_context_hook returns empty dict on empty stdout"""
        workflow = _make_extended_workflow()
        dispatcher, _, _, _ = _make_dispatcher(workflow)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        with patch("ghdag.workflow.dispatcher.subprocess.run", return_value=mock_result):
            result = dispatcher._run_context_hook("echo test", 10)

        assert result == {}

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
            "ghdag.workflow.dispatcher.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="hook", timeout=30),
        ):
            with pytest.raises(subprocess.TimeoutExpired):
                dispatcher._run_context_hook("slow-cmd", 10)

    def test_dispatch_aborts_when_context_hook_fails(self):
        """dispatch does not enqueue or transition labels when context_hook fails"""
        workflow = WorkflowConfig(
            name="test",
            triggers=[TriggerConfig(label="pipeline:draft-ready", handler="brushup")],
            handlers={
                "brushup": HandlerConfig(
                    steps=[StepConfig(template="brushup", model="opus")],
                    context_hook="fail-hook",
                ),
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
                dispatcher.dispatch(
                    issue, workflow, handler, trigger=trigger, trigger_rank=0,
                )

        assert not submit_calls
        assert not github_client.updated_labels
        assert order_builder.build_calls == 0

    def test_polling_writes_audit_on_context_hook_failure(self, tmp_path):
        """run() records dispatcher_dispatch_failed when context_hook raises"""
        workflow = WorkflowConfig(
            name="test-pipeline",
            triggers=[TriggerConfig(label="pipeline:draft-ready", handler="brushup")],
            handlers={
                "brushup": HandlerConfig(
                    steps=[StepConfig(template="brushup", model="opus")],
                    context_hook="fail-hook",
                ),
            },
            polling_interval=0,
        )
        queue_dir = str(tmp_path)
        dispatcher, github_client, _, _ = _make_dispatcher(workflow, queue_dir=queue_dir)
        issue = _make_issue(42, ["pipeline:draft-ready"])
        github_client.list_all_issues_return = [issue]

        mock_result = MagicMock()
        mock_result.returncode = 7
        mock_result.stderr = "hook failed"
        with patch("ghdag.workflow.dispatcher.subprocess.run", return_value=mock_result):
            dispatcher.run(max_iterations=1)

        audit_path = tmp_path / "audit.jsonl"
        assert audit_path.exists()
        events = [
            json.loads(line)
            for line in audit_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        failed_events = [e for e in events if e.get("event") == "dispatcher_dispatch_failed"]
        assert len(failed_events) == 1
        evt = failed_events[0]
        assert evt["exception_class"] == "ContextHookError"
        assert "rc=7" in evt["exception_message"]
        assert "hook failed" in evt["exception_message"]

