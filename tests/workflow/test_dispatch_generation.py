"""Tests for generation-aware idempotency keys in WorkflowDispatcher."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ghdag.github_client import GitHubIssuePort
from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
from ghdag.pipeline.order import TemplateOrderBuilder
from ghdag.pipeline.state import PipelineState, build_idempotency_key
from ghdag.workflow.dispatcher import WorkflowDispatcher
from ghdag.workflow.schema import HandlerConfig, StepConfig, TriggerConfig, WorkflowConfig


def _make_workflow(name: str = "issuesmith") -> WorkflowConfig:
    return WorkflowConfig(
        name=name,
        triggers=[TriggerConfig(label="develop-ready", handler="impl")],
        handlers={
            "impl": HandlerConfig(
                steps=[StepConfig(id="p1", template="p1", model="claude-opus-4-6")],
            ),
        },
        polling_interval=0,
    )


def _make_dispatcher(tmp_path: Path) -> tuple[WorkflowDispatcher, PipelineState, MagicMock]:
    exec_jsonl = tmp_path / "jobs" / "exec.jsonl"
    exec_jsonl.parent.mkdir(parents=True)
    exec_jsonl.write_text("", encoding="utf-8")
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "p1.md").write_text("order ${issue_number}", encoding="utf-8")
    state = PipelineState(state_dir=tmp_path / ".pipeline-state", exec_jsonl_path=exec_jsonl)
    pipeline = LLMPipelineAPI(
        pipeline_state=state,
        order_builder=TemplateOrderBuilder(str(templates)),
        queue_dir=str(exec_jsonl.parent),
    )
    github = MagicMock(spec=GitHubIssuePort)
    github.get_issue_comments.return_value = []
    dispatcher = WorkflowDispatcher(
        workflows=[_make_workflow()],
        github_client=github,
        pipeline=pipeline,
        queue_dir=str(exec_jsonl.parent),
    )
    return dispatcher, state, github


class TestBuildIdempotencyKey:
    def test_generation_zero_uses_legacy_format(self):
        assert build_idempotency_key("issuesmith", "impl", 2876, 0) == "issuesmith:impl:2876"

    def test_generation_one_appends_suffix(self):
        assert build_idempotency_key("issuesmith", "impl", 2876, 1) == "issuesmith:impl:2876:1"

    def test_generation_two_appends_suffix(self):
        assert build_idempotency_key("issuesmith", "impl", 2876, 2) == "issuesmith:impl:2876:2"


class TestDispatchGeneration:
    def test_second_dispatch_is_skipped(self, tmp_path):
        dispatcher, state, github = _make_dispatcher(tmp_path)
        workflow = _make_workflow()
        handler = workflow.handlers["impl"]
        trigger = workflow.triggers[0]
        issue = {"number": 2876, "labels": []}

        result1 = dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)
        assert result1.status == "dispatched"

        result2 = dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)
        assert result2.status == "skipped"
        assert result2.reason == "already dispatched"

    def test_generation_zero_key_matches_existing_exec_jsonl(self, tmp_path):
        dispatcher, state, _ = _make_dispatcher(tmp_path)
        key = build_idempotency_key("issuesmith", "impl", 2822, 0)
        state.append_exec_records([
            {"uuid": "u1", "command": "echo", "idempotency_key": key},
        ])
        assert state.check_idempotency(key) is False

    def test_redispatch_uses_incremented_generation(self, tmp_path):
        dispatcher, state, github = _make_dispatcher(tmp_path)
        workflow = _make_workflow()
        handler = workflow.handlers["impl"]
        trigger = workflow.triggers[0]
        issue = {"number": 2876, "labels": []}

        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)
        result = dispatcher.dispatch(
            issue, workflow, handler, trigger=trigger, trigger_rank=0,
            redispatch=True, redispatch_reason="worktree lost",
        )
        assert result.status == "dispatched"
        assert state.get_generation("issuesmith", "impl", 2876) == 1

        content = state._exec_jsonl_path.read_text(encoding="utf-8")
        assert "issuesmith:impl:2876:1" in content

    def test_skipped_logs_warning_with_guidance(self, tmp_path, caplog):
        dispatcher, state, github = _make_dispatcher(tmp_path)
        workflow = _make_workflow()
        handler = workflow.handlers["impl"]
        trigger = workflow.triggers[0]
        issue = {"number": 2822, "labels": []}

        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)
        with caplog.at_level(logging.WARNING, logger="ghdag.workflow.dispatcher"):
            dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)

        warning_text = caplog.text
        assert "already dispatched" in warning_text
        assert "issue=#2822" in warning_text
        assert "handler=impl" in warning_text
        assert "issuesmith:impl:2822" in warning_text
        assert "ghdag dag recover" in warning_text
        assert "ghdag trigger" in warning_text
        assert "--redispatch" in warning_text


class TestRemoveIdempotencyMatchingGenerations:
    def test_removes_generation_zero_through_n(self, tmp_path):
        state = PipelineState(
            state_dir=tmp_path / "state",
            exec_jsonl_path=tmp_path / "exec.jsonl",
        )
        state._exec_jsonl_path.write_text(
            json.dumps({"uuid": "u0", "idempotency_key": "wf:impl:42"}) + "\n"
            + json.dumps({"uuid": "u1", "idempotency_key": "wf:impl:42:1"}) + "\n"
            + json.dumps({"uuid": "u2", "idempotency_key": "wf:impl:42:2"}) + "\n"
            + json.dumps({"uuid": "u3", "idempotency_key": "wf:impl:99"}) + "\n",
            encoding="utf-8",
        )
        removed = state.remove_idempotency_matching("wf", 42)
        assert removed == 3
        remaining = state._exec_jsonl_path.read_text(encoding="utf-8")
        assert "impl:42" not in remaining
        assert "impl:99" in remaining
