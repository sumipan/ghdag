"""Tests for WorkflowDispatcher hot-reload and dispatch-failed audit (AC-1, AC-2, AC-4, AC-5, AC-8) — Issue #3431."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

from ghdag.github_client import GitHubIssuePort
from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
from ghdag.workflow.dispatcher import WorkflowDispatcher
from ghdag.workflow.loader import load_workflows
from ghdag.workflow.schema import HandlerConfig, StepConfig, TriggerConfig, WorkflowConfig


def _make_github_mock():
    github_client = MagicMock(spec=GitHubIssuePort)
    github_client.list_all_issues.return_value = []
    github_client.get_last_rate_limit.return_value = None
    return github_client


def _make_pipeline_mock():
    return MagicMock(spec=LLMPipelineAPI)


_INITIAL_YAML = """\
name: test-workflow
triggers:
  - label: "test:ready"
    handler: brushup
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-opus-4-6
polling_interval: 0
"""

_UPDATED_YAML = """\
name: test-workflow
triggers:
  - label: "test:ready"
    handler: brushup
  - label: "test:develop-ready"
    handler: impl
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-opus-4-6
  impl:
    steps:
      - template: impl
        model: claude-sonnet-4-6
polling_interval: 0
"""


def _setup_workflow_dir(tmp_path: Path, yaml_content: str, templates: list[str]) -> Path:
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir(exist_ok=True)
    (workflows_dir / "test.yml").write_text(yaml_content)
    tdir = workflows_dir / "templates"
    tdir.mkdir(exist_ok=True)
    for name in templates:
        (tdir / f"{name}.md").write_text(f"# {name}\n")
    return workflows_dir


class TestAC1HotReload:
    def test_reload_on_mtime_change(self, tmp_path):
        """AC-1: modifying yml causes dispatcher to pick up new workflow."""
        workflows_dir = _setup_workflow_dir(tmp_path, _INITIAL_YAML, ["brushup"])
        initial_workflows = load_workflows(workflows_dir)

        dispatcher = WorkflowDispatcher(
            workflows=initial_workflows,
            workflows_dir=workflows_dir,
            github_client=_make_github_mock(),
            pipeline=_make_pipeline_mock(),
            queue_dir=str(tmp_path),
        )

        assert len(dispatcher._workflows[0].handlers) == 1

        # Update yml and add new template
        (workflows_dir / "test.yml").write_text(_UPDATED_YAML)
        (workflows_dir / "templates" / "impl.md").write_text("# impl\n")

        dispatcher._maybe_reload_workflows()

        assert len(dispatcher._workflows[0].handlers) == 2
        assert "impl" in dispatcher._workflows[0].handlers

    def test_no_reload_when_mtime_unchanged(self, tmp_path):
        """AC-1: if mtime hasn't changed, workflows are NOT reloaded."""
        workflows_dir = _setup_workflow_dir(tmp_path, _INITIAL_YAML, ["brushup"])
        initial_workflows = load_workflows(workflows_dir)

        dispatcher = WorkflowDispatcher(
            workflows=initial_workflows,
            workflows_dir=workflows_dir,
            github_client=_make_github_mock(),
            pipeline=_make_pipeline_mock(),
            queue_dir=str(tmp_path),
        )

        original_workflows = dispatcher._workflows
        dispatcher._maybe_reload_workflows()

        assert dispatcher._workflows is original_workflows


class TestAC2InvalidYamlFallback:
    def test_invalid_yaml_keeps_old_workflows(self, tmp_path):
        """AC-2: invalid yml keeps previous valid workflows."""
        workflows_dir = _setup_workflow_dir(tmp_path, _INITIAL_YAML, ["brushup"])
        initial_workflows = load_workflows(workflows_dir)

        dispatcher = WorkflowDispatcher(
            workflows=initial_workflows,
            workflows_dir=workflows_dir,
            github_client=_make_github_mock(),
            pipeline=_make_pipeline_mock(),
            queue_dir=str(tmp_path),
        )

        original_workflows = dispatcher._workflows

        # Write invalid YAML
        (workflows_dir / "test.yml").write_text("invalid: yaml: {broken")

        dispatcher._maybe_reload_workflows()

        assert dispatcher._workflows is original_workflows

    def test_invalid_yaml_logs_warning(self, tmp_path, caplog):
        """AC-2: reload failure emits a WARNING log."""
        workflows_dir = _setup_workflow_dir(tmp_path, _INITIAL_YAML, ["brushup"])
        initial_workflows = load_workflows(workflows_dir)

        dispatcher = WorkflowDispatcher(
            workflows=initial_workflows,
            workflows_dir=workflows_dir,
            github_client=_make_github_mock(),
            pipeline=_make_pipeline_mock(),
            queue_dir=str(tmp_path),
        )

        (workflows_dir / "test.yml").write_text("invalid: yaml: {broken")

        with caplog.at_level(logging.WARNING, logger="ghdag.workflow.dispatcher"):
            dispatcher._maybe_reload_workflows()

        assert any(
            "reload" in r.message.lower() or "failed" in r.message.lower()
            for r in caplog.records
        )

    def test_invalid_yaml_run_does_not_crash(self, tmp_path):
        """AC-2: dispatcher.run() does not crash when yml becomes invalid mid-run."""
        workflows_dir = _setup_workflow_dir(tmp_path, _INITIAL_YAML, ["brushup"])
        initial_workflows = load_workflows(workflows_dir)

        dispatcher = WorkflowDispatcher(
            workflows=initial_workflows,
            workflows_dir=workflows_dir,
            github_client=_make_github_mock(),
            pipeline=_make_pipeline_mock(),
            queue_dir=str(tmp_path),
        )

        # Corrupt the yml before run
        (workflows_dir / "test.yml").write_text("invalid: yaml: {broken")

        dispatcher.run(max_iterations=1)  # must not raise


class TestAC4DispatchFailedAuditEvent:
    def test_audit_event_written_on_dispatch_failure(self, tmp_path):
        """AC-4: dispatch failure writes dispatcher_dispatch_failed event to audit.jsonl."""
        workflows_dir = _setup_workflow_dir(tmp_path, _INITIAL_YAML, ["brushup"])
        initial_workflows = load_workflows(workflows_dir)

        github_client = _make_github_mock()
        issue = {
            "number": 42,
            "title": "Test",
            "body": "",
            "labels": [{"name": "test:ready"}],
            "url": "",
        }
        github_client.list_all_issues.return_value = [issue]

        dispatcher = WorkflowDispatcher(
            workflows=initial_workflows,
            workflows_dir=workflows_dir,
            github_client=github_client,
            pipeline=_make_pipeline_mock(),
            queue_dir=str(tmp_path),
        )
        dispatcher.dispatch = MagicMock(side_effect=RuntimeError("template expansion failed"))

        dispatcher.run(max_iterations=1)

        audit_path = tmp_path / "audit.jsonl"
        assert audit_path.exists()
        events = [
            json.loads(line)
            for line in audit_path.read_text().splitlines()
            if line.strip()
        ]
        failed_events = [e for e in events if e.get("event") == "dispatcher_dispatch_failed"]
        assert len(failed_events) == 1
        evt = failed_events[0]
        assert evt["issue_number"] == 42
        assert evt["handler"] == "brushup"
        assert "exception_class" in evt
        assert evt["exception_class"] == "RuntimeError"
        assert "exception_message" in evt
        assert "consecutive_failures" in evt
        assert evt["consecutive_failures"] == 1

    def test_audit_event_schema_version(self, tmp_path):
        """AC-4: audit event includes schema_version and timestamp."""
        workflows_dir = _setup_workflow_dir(tmp_path, _INITIAL_YAML, ["brushup"])
        initial_workflows = load_workflows(workflows_dir)

        github_client = _make_github_mock()
        issue = {"number": 1, "title": "T", "body": "", "labels": [{"name": "test:ready"}], "url": ""}
        github_client.list_all_issues.return_value = [issue]

        dispatcher = WorkflowDispatcher(
            workflows=initial_workflows,
            workflows_dir=workflows_dir,
            github_client=github_client,
            pipeline=_make_pipeline_mock(),
            queue_dir=str(tmp_path),
        )
        dispatcher.dispatch = MagicMock(side_effect=ValueError("boom"))

        dispatcher.run(max_iterations=1)

        events = [
            json.loads(line)
            for line in (tmp_path / "audit.jsonl").read_text().splitlines()
            if line.strip()
        ]
        evt = next(e for e in events if e.get("event") == "dispatcher_dispatch_failed")
        assert evt.get("schema_version") == 1
        assert "timestamp" in evt


class TestAC5ConsecutiveFailures:
    def test_consecutive_failures_increments(self, tmp_path):
        """AC-5: consecutive_failures increments across multiple failed dispatches."""
        workflows_dir = _setup_workflow_dir(tmp_path, _INITIAL_YAML, ["brushup"])
        initial_workflows = load_workflows(workflows_dir)

        github_client = _make_github_mock()
        issue = {"number": 42, "title": "T", "body": "", "labels": [{"name": "test:ready"}], "url": ""}
        github_client.list_all_issues.return_value = [issue]

        dispatcher = WorkflowDispatcher(
            workflows=initial_workflows,
            workflows_dir=workflows_dir,
            github_client=github_client,
            pipeline=_make_pipeline_mock(),
            queue_dir=str(tmp_path),
        )
        dispatcher.dispatch = MagicMock(side_effect=RuntimeError("boom"))

        dispatcher.run(max_iterations=3)

        events = [
            json.loads(line)
            for line in (tmp_path / "audit.jsonl").read_text().splitlines()
            if line.strip()
        ]
        failed_events = [e for e in events if e.get("event") == "dispatcher_dispatch_failed"]
        assert len(failed_events) == 3
        counts = [e["consecutive_failures"] for e in failed_events]
        assert counts == [1, 2, 3]

    def test_consecutive_failures_resets_on_success(self, tmp_path):
        """AC-5: consecutive_failures resets to 0 after a successful dispatch."""
        workflows_dir = _setup_workflow_dir(tmp_path, _INITIAL_YAML, ["brushup"])
        initial_workflows = load_workflows(workflows_dir)

        github_client = _make_github_mock()
        issue = {"number": 10, "title": "T", "body": "", "labels": [{"name": "test:ready"}], "url": ""}
        github_client.list_all_issues.return_value = [issue]

        dispatcher = WorkflowDispatcher(
            workflows=initial_workflows,
            workflows_dir=workflows_dir,
            github_client=github_client,
            pipeline=_make_pipeline_mock(),
            queue_dir=str(tmp_path),
        )

        from ghdag.workflow.schema import DispatchResult

        call_count = 0

        def fail_then_succeed(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("first failure")
            return DispatchResult(status="dispatched")

        dispatcher.dispatch = MagicMock(side_effect=fail_then_succeed)

        dispatcher.run(max_iterations=2)

        key = (10, "brushup")
        assert dispatcher._consecutive_failures.get(key, 0) == 0


class TestAC8BackwardCompatibility:
    def test_dispatcher_without_workflows_dir(self):
        """AC-8: WorkflowDispatcher without workflows_dir still works."""
        workflow = WorkflowConfig(
            name="test",
            triggers=[TriggerConfig(label="test:ready", handler="brushup")],
            handlers={
                "brushup": HandlerConfig(
                    steps=[StepConfig(template="brushup", model="claude-opus-4-6")]
                )
            },
            polling_interval=0,
        )
        github_client = _make_github_mock()
        pipeline = _make_pipeline_mock()

        dispatcher = WorkflowDispatcher(
            workflows=[workflow],
            github_client=github_client,
            pipeline=pipeline,
            queue_dir="queue",
        )

        assert dispatcher._workflows_dir is None
        dispatcher.run(max_iterations=1)  # must not raise

    def test_workflows_dir_is_none_by_default(self):
        """AC-8: workflows_dir defaults to None (existing tests unaffected)."""
        workflow = WorkflowConfig(
            name="test",
            triggers=[TriggerConfig(label="x:ready", handler="h")],
            handlers={"h": HandlerConfig(steps=[StepConfig(template="t", model="m")])},
            polling_interval=0,
        )
        dispatcher = WorkflowDispatcher(
            workflows=[workflow],
            github_client=_make_github_mock(),
            pipeline=_make_pipeline_mock(),
            queue_dir="queue",
        )
        assert dispatcher._workflows_dir is None
