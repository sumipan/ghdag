"""Tests for AuditHooks — Issue #1090 AC-1 through AC-9."""

from __future__ import annotations

import json

import pytest

from ghdag.core.models.metrics import FailureClass, TaskMetrics
from ghdag.dag.audit_hooks import AuditHooks
from ghdag.dag.hooks import DefaultHooks
from ghdag.dag.models import Task

UUID = "test-uuid-0000-0000-0000-000000000001"
_FIXED_STARTED_AT = 1_700_000_000.0


def _make_task() -> Task:
    return Task(uuid=UUID, command="echo hello")


def _make_metrics(status: str = "success") -> TaskMetrics:
    return TaskMetrics(
        uuid=UUID,
        engine="claude",
        model="claude-sonnet-4-6",
        wall_time_sec=10.5,
        token_count=1500,
        status=status,
        started_at=_FIXED_STARTED_AT,
        finished_at=_FIXED_STARTED_AT + 10.5,
    )


class TestAuditHooks:
    def test_audit_hooks_extends_default_hooks(self):
        assert issubclass(AuditHooks, DefaultHooks)

    def test_no_args_init_works(self):
        hooks = AuditHooks()
        task = _make_task()
        metrics = _make_metrics()
        hooks.on_task_success(UUID, task, metrics)
        hooks.on_task_failure(UUID, task, 1, "err", metrics)
        hooks.on_task_rejected(UUID, task, 0, False, metrics)
        hooks.on_task_dep_failed(UUID, task, "dep")
        hooks.on_task_empty_result(UUID, task, "", metrics)

    def _make_metrics_with_correlation(self, status: str = "success", correlation_id: str | None = None) -> TaskMetrics:
        return TaskMetrics(
            uuid=UUID,
            engine="claude",
            model="claude-sonnet-4-6",
            wall_time_sec=10.5,
            token_count=1500,
            status=status,
            started_at=_FIXED_STARTED_AT,
            finished_at=_FIXED_STARTED_AT + 10.5,
            correlation_id=correlation_id,
        )

    def test_on_task_failure_correlation_id(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        hooks = AuditHooks(audit_path=audit_path)
        hooks.on_task_failure(UUID, _make_task(), 1, "error", self._make_metrics_with_correlation("failure", "test:key"))

        r = json.loads(audit_path.read_text().strip())
        assert r["correlation_id"] == "test:key"

    def test_on_task_rejected_correlation_id(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        hooks = AuditHooks(audit_path=audit_path)
        hooks.on_task_rejected(UUID, _make_task(), 0, False, self._make_metrics_with_correlation("rejected", "test:key"))

        r = json.loads(audit_path.read_text().strip())
        assert r["correlation_id"] == "test:key"

    def test_on_task_empty_result_correlation_id(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        hooks = AuditHooks(audit_path=audit_path)
        hooks.on_task_empty_result(UUID, _make_task(), "", self._make_metrics_with_correlation("empty_result", "test:key"))

        r = json.loads(audit_path.read_text().strip())
        assert r["correlation_id"] == "test:key"

    def _make_metrics_with_failure_class(self, status: str = "failure", failure_class: FailureClass | None = None) -> TaskMetrics:
        return TaskMetrics(
            uuid=UUID,
            engine="claude",
            model="claude-sonnet-4-6",
            wall_time_sec=10.5,
            token_count=1500,
            status=status,
            started_at=_FIXED_STARTED_AT,
            finished_at=_FIXED_STARTED_AT + 10.5,
            failure_class=failure_class,
        )

    def test_dep_failed_failure_class(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        hooks = AuditHooks(audit_path=audit_path)
        hooks.on_task_dep_failed(UUID, _make_task(), "dep-uuid")

        r = json.loads(audit_path.read_text().strip())
        assert r["failure_class"] == "DEP_FAILED"

    def test_on_task_start_no_audit_path_no_error(self):
        hooks = AuditHooks(audit_path=None)
        hooks.on_task_start(UUID, _make_task())

    def test_on_task_start_no_args_init_no_error(self):
        hooks = AuditHooks()
        hooks.on_task_start(UUID, _make_task())
