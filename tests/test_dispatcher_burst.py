"""Tests for WorkflowDispatcher correlation burst observation."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
from ghdag.workflow.dispatcher import (
    WorkflowDispatcher,
    _BURST_COOLDOWN_SEC,
    _BURST_THRESHOLD,
    _BURST_WINDOW_SEC,
)
from ghdag.workflow.github import GitHubIssuePort
from ghdag.workflow.schema import (
    HandlerConfig,
    StepConfig,
    TriggerConfig,
    WorkflowConfig,
)


def _make_workflow(name: str = "wf") -> WorkflowConfig:
    return WorkflowConfig(
        name=name,
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
    github_client.list_issues.return_value = []
    github_client.get_rate_limit.return_value = None
    pipeline = MagicMock(spec=LLMPipelineAPI)
    dispatcher = WorkflowDispatcher(
        workflows=[_make_workflow()],
        github_client=github_client,
        pipeline=pipeline,
        queue_dir=str(tmp_path),
    )
    return dispatcher, github_client


def _ts(offset_sec: float = 0.0) -> str:
    dt = datetime.fromtimestamp(time.time() - offset_sec, tz=timezone(timedelta(hours=9)))
    return dt.isoformat()


def _write_burst_events(audit_path: Path, cid: str, count: int) -> None:
    with open(audit_path, "w", encoding="utf-8") as f:
        for i in range(count):
            ev = {
                "event_type": "task_complete",
                "uuid": f"u{i}",
                "status": "success",
                "correlation_id": cid,
                "timestamp": _ts(30 + i),
            }
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


class TestObserveCorrelationBurst:
    def test_warning_on_burst(self, tmp_path, caplog):
        dispatcher, _ = _make_dispatcher(tmp_path)
        cid = "issuesmith:B1:burst"
        _write_burst_events(tmp_path / "audit.jsonl", cid, _BURST_THRESHOLD + 2)

        with caplog.at_level(logging.WARNING, logger="ghdag.workflow.dispatcher"):
            dispatcher._observe_correlation_burst()

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert cid in warnings[0].message

    def test_no_warning_below_threshold(self, tmp_path, caplog):
        dispatcher, _ = _make_dispatcher(tmp_path)
        _write_burst_events(tmp_path / "audit.jsonl", "cid-low", _BURST_THRESHOLD - 1)

        with caplog.at_level(logging.WARNING, logger="ghdag.workflow.dispatcher"):
            dispatcher._observe_correlation_burst()

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 0

    def test_cooldown_suppresses_repeated_warning(self, tmp_path, caplog):
        dispatcher, _ = _make_dispatcher(tmp_path)
        cid = "issuesmith:B1:cooldown"
        _write_burst_events(tmp_path / "audit.jsonl", cid, _BURST_THRESHOLD + 1)

        now = time.time()
        dispatcher._burst_warned[cid] = now - 100  # within cooldown

        with caplog.at_level(logging.WARNING, logger="ghdag.workflow.dispatcher"):
            dispatcher._observe_correlation_burst()

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 0

    def test_warning_after_cooldown_expires(self, tmp_path, caplog):
        dispatcher, _ = _make_dispatcher(tmp_path)
        cid = "issuesmith:B1:expired"
        _write_burst_events(tmp_path / "audit.jsonl", cid, _BURST_THRESHOLD + 1)

        dispatcher._burst_warned[cid] = time.time() - _BURST_COOLDOWN_SEC - 1

        with caplog.at_level(logging.WARNING, logger="ghdag.workflow.dispatcher"):
            dispatcher._observe_correlation_burst()

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert cid in warnings[0].message

    def test_exception_does_not_propagate(self, tmp_path, caplog):
        dispatcher, _ = _make_dispatcher(tmp_path)

        with patch(
            "ghdag.workflow.dispatcher.detect_correlation_bursts",
            side_effect=RuntimeError("boom"),
        ):
            with caplog.at_level(logging.WARNING, logger="ghdag.workflow.dispatcher"):
                dispatcher._observe_correlation_burst()

    def test_run_calls_observe_correlation_burst(self, tmp_path):
        dispatcher, github_client = _make_dispatcher(tmp_path)
        _write_burst_events(tmp_path / "audit.jsonl", "cid-run", _BURST_THRESHOLD + 1)

        with patch.object(dispatcher, "_observe_correlation_burst") as mock_burst:
            with patch.object(dispatcher, "_observe_rate_limit"):
                dispatcher.run(max_iterations=1)

        mock_burst.assert_called_once()

    def test_burst_warned_initialized_in_init(self, tmp_path):
        dispatcher, _ = _make_dispatcher(tmp_path)
        assert dispatcher._burst_warned == {}
