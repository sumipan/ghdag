"""Tests for WorkflowDispatcher correlation burst observation."""

from __future__ import annotations

import json
import logging
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ghdag.core.models.workflow import (
    HandlerConfig,
    StepConfig,
    TriggerConfig,
    WorkflowConfig,
)
from ghdag.github_client import GitHubIssuePort
from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
from ghdag.workflow.dispatcher import (
    _BURST_THRESHOLD,
    WorkflowDispatcher,
)

FIXED_NOW = 1_762_600_000.0
_JST = timezone(timedelta(hours=9))


@pytest.fixture(autouse=True)
def _fixed_audit_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = types.SimpleNamespace(time=lambda: FIXED_NOW)
    monkeypatch.setattr("ghdag.workflow.dispatcher.time", fake)
    monkeypatch.setattr("ghdag.io.audit_query.time", fake)


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
    github_client.list_all_issues.return_value = []
    github_client.get_last_rate_limit.return_value = None
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
    dt = datetime.fromtimestamp(FIXED_NOW - offset_sec, tz=_JST)
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

    def test_cooldown_suppresses_repeated_warning(self, tmp_path, caplog):
        dispatcher, _ = _make_dispatcher(tmp_path)
        cid = "issuesmith:B1:cooldown"
        _write_burst_events(tmp_path / "audit.jsonl", cid, _BURST_THRESHOLD + 1)

        dispatcher._burst_warned[cid] = FIXED_NOW - 100  # within cooldown

        with caplog.at_level(logging.WARNING, logger="ghdag.workflow.dispatcher"):
            dispatcher._observe_correlation_burst()

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 0

    def test_exception_does_not_propagate(self, tmp_path, caplog):
        dispatcher, _ = _make_dispatcher(tmp_path)

        with patch(
            "ghdag.workflow.dispatcher.detect_correlation_bursts",
            side_effect=RuntimeError("boom"),
        ):
            with caplog.at_level(logging.WARNING, logger="ghdag.workflow.dispatcher"):
                dispatcher._observe_correlation_burst()

    def test_burst_warned_initialized_in_init(self, tmp_path):
        dispatcher, _ = _make_dispatcher(tmp_path)
        assert dispatcher._burst_warned == {}
