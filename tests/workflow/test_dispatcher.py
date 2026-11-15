"""Tests for WorkflowDispatcher rate-limit poll skipping (nexus #3752)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from ghdag.core.exceptions import RateLimitError
from ghdag.core.models.workflow import (
    HandlerConfig,
    StepConfig,
    TriggerConfig,
    WorkflowConfig,
)
from ghdag.github_client import GitHubIssuePort
from ghdag.pipeline.llm_pipeline import LLMPipelineAPI, build_order_builders
from ghdag.workflow.dispatcher import WorkflowDispatcher
from ghdag.workflow.loader import load_workflows

_FIXED_WALL_EPOCH = 1_700_000_000


def _make_workflow() -> WorkflowConfig:
    return WorkflowConfig(
        name="wf",
        triggers=[TriggerConfig(label="wf:ready", handler="h")],
        handlers={
            "h": HandlerConfig(
                steps=[StepConfig(template="t", model="claude-opus-4-6")],
            ),
        },
        polling_interval=0,
    )


def _make_dispatcher(tmp_path: Path) -> tuple[WorkflowDispatcher, MagicMock]:
    github_client = MagicMock(spec=GitHubIssuePort)
    github_client.list_all_issues.return_value = []
    github_client.get_last_rate_limit.return_value = None
    dispatcher = WorkflowDispatcher(
        workflows=[_make_workflow()],
        github_client=github_client,
        pipeline=MagicMock(spec=LLMPipelineAPI),
        queue_dir=str(tmp_path),
    )
    return dispatcher, github_client


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def test_once_rate_limited_logs_skip_line_without_raising(tmp_path, caplog, monkeypatch):
    """AC-2: watch --once (max_iterations=1) logs one skip line and returns normally."""
    monkeypatch.setattr(
        "ghdag.workflow.dispatcher.time.time", lambda: _FIXED_WALL_EPOCH,
    )
    dispatcher, github = _make_dispatcher(tmp_path)
    reset_at = _FIXED_WALL_EPOCH + 600
    github.list_all_issues.side_effect = RateLimitError(
        "rate limited", status_code=403, reset_at=reset_at
    )

    with caplog.at_level(logging.INFO, logger="ghdag.workflow.dispatcher"):
        dispatcher.run(max_iterations=1)

    skip_lines = [
        r.getMessage() for r in caplog.records if "rate limited: skip poll until" in r.getMessage()
    ]
    assert skip_lines == [f"rate limited: skip poll until {_iso(reset_at)}"]
    assert dispatcher._rate_limited_until == reset_at


def test_poll_skipped_until_reset(tmp_path, caplog, monkeypatch):
    """Subsequent iterations do not call the API before reset_at."""
    monkeypatch.setattr(
        "ghdag.workflow.dispatcher.time.time", lambda: _FIXED_WALL_EPOCH,
    )
    dispatcher, github = _make_dispatcher(tmp_path)
    reset_at = _FIXED_WALL_EPOCH + 600
    github.list_all_issues.side_effect = RateLimitError(
        "rate limited", status_code=403, reset_at=reset_at
    )

    with caplog.at_level(logging.INFO, logger="ghdag.workflow.dispatcher"):
        dispatcher.run(max_iterations=3)

    assert github.list_all_issues.call_count == 1
    skip_lines = [
        r for r in caplog.records if "rate limited: skip poll until" in r.getMessage()
    ]
    assert len(skip_lines) == 3


def test_poll_resumes_after_reset(tmp_path, monkeypatch):
    """The first iteration past reset_at polls normally and clears the state."""
    monkeypatch.setattr(
        "ghdag.workflow.dispatcher.time.time", lambda: _FIXED_WALL_EPOCH,
    )
    dispatcher, github = _make_dispatcher(tmp_path)
    dispatcher._rate_limited_until = _FIXED_WALL_EPOCH - 1

    dispatcher.run(max_iterations=1)

    github.list_all_issues.assert_called_once_with("open")
    assert dispatcher._rate_limited_until is None


def test_rate_limit_without_reset_does_not_skip(tmp_path):
    dispatcher, github = _make_dispatcher(tmp_path)
    github.list_all_issues.side_effect = RateLimitError("rate limited", status_code=403)

    dispatcher.run(max_iterations=2)

    assert github.list_all_issues.call_count == 2
    assert dispatcher._rate_limited_until is None


def _two_matches(dispatcher: WorkflowDispatcher) -> list[dict]:
    workflow = dispatcher._workflows[0]
    trigger = workflow.triggers[0]
    handler = workflow.handlers[trigger.handler]
    base = {
        "_workflow": workflow,
        "_handler": handler,
        "_trigger": trigger,
        "_trigger_rank": 0,
        "_github": dispatcher._githubs[0],
    }
    return [
        {**base, "_issue_data": {"number": 1}, "handler": trigger.handler},
        {**base, "_issue_data": {"number": 2}, "handler": trigger.handler},
    ]


def test_dispatch_rate_limit_sets_skip_and_stops_remaining_matches(tmp_path, caplog, monkeypatch):
    monkeypatch.setattr(
        "ghdag.workflow.dispatcher.time.time", lambda: _FIXED_WALL_EPOCH,
    )
    dispatcher, _github = _make_dispatcher(tmp_path)
    reset_at = _FIXED_WALL_EPOCH + 600
    matches = _two_matches(dispatcher)
    dispatcher.poll_once = MagicMock(return_value=matches)  # type: ignore[method-assign]
    dispatcher.dispatch = MagicMock(  # type: ignore[method-assign]
        side_effect=RateLimitError("rate limited", status_code=403, reset_at=reset_at),
    )

    with caplog.at_level(logging.WARNING, logger="ghdag.workflow.dispatcher"):
        dispatcher.run(max_iterations=1)

    dispatcher.dispatch.assert_called_once()
    assert dispatcher._rate_limited_until == reset_at
    audit_path = tmp_path / "audit.jsonl"
    assert audit_path.exists()
    events = [
        json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()
    ]
    failed = [e for e in events if e.get("event") == "dispatcher_dispatch_failed"]
    assert len(failed) == 1
    assert failed[0]["exception_class"] == "RateLimitError"
    skip_lines = [
        r.getMessage() for r in caplog.records if "rate limited: skip poll until" in r.getMessage()
    ]
    assert len(skip_lines) == 1


def test_dispatch_rate_limit_without_reset_continues(tmp_path):
    dispatcher, _github = _make_dispatcher(tmp_path)
    matches = _two_matches(dispatcher)
    dispatcher.poll_once = MagicMock(return_value=matches)  # type: ignore[method-assign]
    dispatcher.dispatch = MagicMock(  # type: ignore[method-assign]
        side_effect=RateLimitError("rate limited", status_code=403),
    )

    dispatcher.run(max_iterations=1)

    assert dispatcher.dispatch.call_count == 2
    assert dispatcher._rate_limited_until is None


def _write_workflow_without_template_dir(wf_dir: Path, *, marker: str) -> None:
    tdir = wf_dir / "templates"
    tdir.mkdir(parents=True)
    (tdir / "p1.md").write_text(marker, encoding="utf-8")
    yaml_content = """\
name: cwd-wf
triggers:
  - label: "pipe:ready"
    handler: run
handlers:
  run:
    steps:
      - id: step1
        template: p1
        model: bash
        engine: shell
"""
    (wf_dir / "wf.yml").write_text(yaml_content, encoding="utf-8")


def test_dispatch_uses_workflow_dir_templates_when_template_dir_omitted(
    tmp_path: Path, monkeypatch,
) -> None:
    """Refs sumipan/nexus#6803 — dispatch reads templates under the workflow YAML directory."""
    marker = "echo wf-dir-marker\n"
    wf_dir = tmp_path / "wf"
    wf_dir.mkdir()
    other_cwd = tmp_path / "other"
    other_cwd.mkdir()
    _write_workflow_without_template_dir(wf_dir, marker=marker)

    queue = tmp_path / "queue"
    queue.mkdir()
    monkeypatch.chdir(other_cwd)

    configs = load_workflows(wf_dir)
    workflow = configs[0]
    order_builders = build_order_builders(configs)

    captured: list[str] = []

    def _capture_write(ts, step_uuid, order_content, queue_dir, engine="claude"):
        captured.append(order_content)
        return f"{ts}-{engine}-order-{step_uuid}.md"

    pipeline_state = MagicMock()
    pipeline_state.check_idempotency.return_value = True
    pipeline_state.write_order_file.side_effect = _capture_write
    pipeline = LLMPipelineAPI(
        pipeline_state=pipeline_state,
        order_builder=order_builders[workflow.name],
        queue_dir=str(queue),
        order_builders=order_builders,
    )
    github = MagicMock(spec=GitHubIssuePort)
    dispatcher = WorkflowDispatcher(
        workflows=[workflow],
        github_client=github,
        pipeline=pipeline,
        queue_dir=str(queue),
    )
    issue = {
        "number": 11,
        "title": "t",
        "body": "",
        "labels": [{"name": "pipe:ready"}],
        "url": "https://example/issues/11",
    }
    result = dispatcher.dispatch(
        issue,
        workflow,
        workflow.handlers["run"],
        trigger=workflow.triggers[0],
        trigger_rank=0,
    )
    assert result.status == "dispatched"
    assert captured == [marker]
