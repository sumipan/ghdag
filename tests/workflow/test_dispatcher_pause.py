"""Tests for WorkflowDispatcher pause-file behavior (Issue #2683)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

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
        triggers=[TriggerConfig(label="wf:ready", handler="h")],
        handlers={
            "h": HandlerConfig(
                steps=[StepConfig(template="t", model="claude-opus-4-6")],
            ),
        },
        polling_interval=0,
    )


def _make_dispatcher(tmp_path: Path, pause_file: Path | None = None) -> WorkflowDispatcher:
    github_client = MagicMock(spec=GitHubIssuePort)
    github_client.list_issues.return_value = []
    github_client.get_rate_limit.return_value = None
    pipeline = MagicMock(spec=LLMPipelineAPI)
    return WorkflowDispatcher(
        workflows=[_make_workflow()],
        github_client=github_client,
        pipeline=pipeline,
        queue_dir=str(tmp_path),
        pause_file=pause_file,
    )


def _read_audit_events(tmp_path: Path) -> list[dict]:
    audit_path = tmp_path / "audit.jsonl"
    if not audit_path.exists():
        return []
    return [json.loads(line) for line in audit_path.read_text().splitlines()]


def test_pause_file_skips_poll_once(tmp_path):
    """pause file が存在する間は poll_once / dispatch を呼ばない。"""
    pause_file = tmp_path / "pause.txt"
    pause_file.write_text("quota exhausted", encoding="utf-8")
    dispatcher = _make_dispatcher(tmp_path, pause_file=pause_file)
    dispatcher.poll_once = MagicMock(return_value=[])  # type: ignore[method-assign]
    dispatcher.dispatch = MagicMock()  # type: ignore[method-assign]

    dispatcher.run(max_iterations=2)

    dispatcher.poll_once.assert_not_called()
    dispatcher.dispatch.assert_not_called()


def test_dispatch_resumes_after_pause_file_removed(tmp_path):
    """pause file を削除すると次ループから poll_once / dispatch が再開する。"""
    pause_file = tmp_path / "pause.txt"
    pause_file.write_text("quota exhausted", encoding="utf-8")
    dispatcher = _make_dispatcher(tmp_path, pause_file=pause_file)
    workflow = dispatcher._workflows[0]
    trigger = workflow.triggers[0]
    handler = workflow.handlers[trigger.handler]
    match = {
        "_issue_data": {"number": 1, "labels": [{"name": trigger.label}]},
        "_workflow": workflow,
        "_handler": handler,
        "_trigger": trigger,
        "_trigger_rank": 0,
        "_github": dispatcher._githubs[0],
    }
    dispatcher.poll_once = MagicMock(return_value=[match])  # type: ignore[method-assign]
    dispatcher.dispatch = MagicMock()  # type: ignore[method-assign]

    def _sleep_and_remove(_: float) -> None:
        if pause_file.exists():
            pause_file.unlink()

    with patch("ghdag.workflow.dispatcher.time.sleep", side_effect=_sleep_and_remove):
        dispatcher.run(max_iterations=2)

    dispatcher.poll_once.assert_called_once()
    dispatcher.dispatch.assert_called_once()


def test_pause_and_resume_events_are_recorded_once(tmp_path):
    """pause / resume イベントは遷移時のみ 1 回ずつ記録される。"""
    pause_file = tmp_path / "pause.txt"
    pause_file.write_text("quota exhausted", encoding="utf-8")
    dispatcher = _make_dispatcher(tmp_path, pause_file=pause_file)
    dispatcher.poll_once = MagicMock(return_value=[])  # type: ignore[method-assign]

    def _sleep_and_remove(_: float) -> None:
        if pause_file.exists():
            pause_file.unlink()

    with patch("ghdag.workflow.dispatcher.time.sleep", side_effect=_sleep_and_remove):
        dispatcher.run(max_iterations=3)

    events = _read_audit_events(tmp_path)
    pause_events = [ev for ev in events if ev.get("event") == "dispatcher_pause"]
    resume_events = [ev for ev in events if ev.get("event") == "dispatcher_resume"]
    assert len(pause_events) == 1
    assert len(resume_events) == 1
    assert pause_events[0]["reason"] == "quota exhausted"
    assert resume_events[0]["reason"] == "pause file removed"


def test_pause_reason_is_truncated_to_500_chars(tmp_path):
    """pause 理由は 500 文字で切り捨てる。"""
    pause_file = tmp_path / "pause.txt"
    pause_file.write_text("x" * 700, encoding="utf-8")
    dispatcher = _make_dispatcher(tmp_path, pause_file=pause_file)
    dispatcher.poll_once = MagicMock(return_value=[])  # type: ignore[method-assign]

    dispatcher.run(max_iterations=1)

    events = _read_audit_events(tmp_path)
    pause_events = [ev for ev in events if ev.get("event") == "dispatcher_pause"]
    assert len(pause_events) == 1
    assert len(pause_events[0]["reason"]) == 500
