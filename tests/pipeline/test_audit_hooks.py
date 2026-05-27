"""Tests for AuditHooks — Issue #1090 AC-1 through AC-9."""

from __future__ import annotations

import json
import time

import pytest

from ghdag.dag.hooks import DefaultHooks
from ghdag.dag.models import Task
from ghdag.metrics.models import FailureClass, TaskMetrics
from ghdag.pipeline.hooks import AuditHooks

UUID = "test-uuid-0000-0000-0000-000000000001"


def _make_task() -> Task:
    return Task(uuid=UUID, command="echo hello")


def _make_metrics(status: str = "success") -> TaskMetrics:
    now = time.time()
    return TaskMetrics(
        uuid=UUID,
        engine="claude",
        model="claude-sonnet-4-6",
        wall_time_sec=10.5,
        token_count=1500,
        status=status,
        started_at=now,
        finished_at=now + 10.5,
    )


class TestAuditHooks:
    def test_ac5_is_subclass_of_default_hooks(self):
        assert issubclass(AuditHooks, DefaultHooks)

    def test_on_task_success(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        hooks = AuditHooks(audit_path=audit_path)
        hooks.on_task_success(UUID, _make_task(), _make_metrics("success"))

        records = [json.loads(line) for line in audit_path.read_text().splitlines()]
        assert len(records) == 1
        r = records[0]
        assert r["event_type"] == "task_complete"
        assert r["uuid"] == UUID
        assert r["status"] == "success"
        assert r["elapsed_sec"] == 10.5
        assert r["token_count"] == 1500
        assert r["model"] == "claude-sonnet-4-6"
        assert r["engine"] == "claude"
        assert r["schema_version"] == 1
        assert "+09:00" in r["timestamp"]

    def test_on_task_failure(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        hooks = AuditHooks(audit_path=audit_path)
        hooks.on_task_failure(UUID, _make_task(), 1, "error", _make_metrics("failure"))

        r = json.loads(audit_path.read_text().strip())
        assert r["event_type"] == "task_failed"
        assert r["status"] == "failure"

    def test_on_task_rejected(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        hooks = AuditHooks(audit_path=audit_path)
        hooks.on_task_rejected(UUID, _make_task(), 0, False, _make_metrics("rejected"))

        r = json.loads(audit_path.read_text().strip())
        assert r["event_type"] == "task_rejected"
        assert r["status"] == "rejected"

    def test_on_task_dep_failed_all_none(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        hooks = AuditHooks(audit_path=audit_path)
        hooks.on_task_dep_failed(UUID, _make_task(), "dep-uuid")

        r = json.loads(audit_path.read_text().strip())
        assert r["event_type"] == "task_dep_failed"
        assert r["status"] == "dep_failed"
        assert r["elapsed_sec"] is None
        assert r["token_count"] is None
        assert r["model"] is None
        assert r["engine"] is None

    def test_on_task_empty_result(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        hooks = AuditHooks(audit_path=audit_path)
        hooks.on_task_empty_result(UUID, _make_task(), "", _make_metrics("empty_result"))

        r = json.loads(audit_path.read_text().strip())
        assert r["event_type"] == "task_empty_result"
        assert r["status"] == "empty_result"

    def test_ac6_audit_path_none_no_write(self, tmp_path):
        hooks = AuditHooks(audit_path=None)
        task = _make_task()
        metrics = _make_metrics()
        hooks.on_task_success(UUID, task, metrics)
        hooks.on_task_failure(UUID, task, 1, "err", metrics)
        hooks.on_task_rejected(UUID, task, 0, False, metrics)
        hooks.on_task_dep_failed(UUID, task, "dep")
        hooks.on_task_empty_result(UUID, task, "", metrics)
        assert not any(tmp_path.iterdir())

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
        now = time.time()
        return TaskMetrics(
            uuid=UUID,
            engine="claude",
            model="claude-sonnet-4-6",
            wall_time_sec=10.5,
            token_count=1500,
            status=status,
            started_at=now,
            finished_at=now + 10.5,
            correlation_id=correlation_id,
        )

    def test_on_task_success_correlation_id(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        hooks = AuditHooks(audit_path=audit_path)
        hooks.on_task_success(UUID, _make_task(), self._make_metrics_with_correlation("success", "test:key"))

        r = json.loads(audit_path.read_text().strip())
        assert r["correlation_id"] == "test:key"

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

    def test_on_task_dep_failed_uses_idempotency_key(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        hooks = AuditHooks(audit_path=audit_path)
        task = Task(uuid=UUID, command="echo hello", idempotency_key="test:key")
        hooks.on_task_dep_failed(UUID, task, "dep-uuid")

        r = json.loads(audit_path.read_text().strip())
        assert r["correlation_id"] == "test:key"

    def test_correlation_id_null_when_not_set(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        hooks = AuditHooks(audit_path=audit_path)
        task = Task(uuid=UUID, command="echo hello")
        hooks.on_task_dep_failed(UUID, task, "dep-uuid")

        r = json.loads(audit_path.read_text().strip())
        assert r.get("correlation_id") is None

    def test_metrics_correlation_id_none_audit_null(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        hooks = AuditHooks(audit_path=audit_path)
        hooks.on_task_success(UUID, _make_task(), self._make_metrics_with_correlation("success", None))

        r = json.loads(audit_path.read_text().strip())
        assert r.get("correlation_id") is None

    def _make_metrics_with_failure_class(self, status: str = "failure", failure_class: FailureClass | None = None) -> TaskMetrics:
        now = time.time()
        return TaskMetrics(
            uuid=UUID,
            engine="claude",
            model="claude-sonnet-4-6",
            wall_time_sec=10.5,
            token_count=1500,
            status=status,
            started_at=now,
            finished_at=now + 10.5,
            failure_class=failure_class,
        )

    def test_failure_class_propagated_from_metrics(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        hooks = AuditHooks(audit_path=audit_path)
        hooks.on_task_failure(UUID, _make_task(), 1, "timeout", self._make_metrics_with_failure_class("failure", FailureClass.TIMEOUT))

        r = json.loads(audit_path.read_text().strip())
        assert r["failure_class"] == "TIMEOUT"

    def test_dep_failed_failure_class(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        hooks = AuditHooks(audit_path=audit_path)
        hooks.on_task_dep_failed(UUID, _make_task(), "dep-uuid")

        r = json.loads(audit_path.read_text().strip())
        assert r["failure_class"] == "DEP_FAILED"

    def test_success_failure_class_null(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        hooks = AuditHooks(audit_path=audit_path)
        hooks.on_task_success(UUID, _make_task(), self._make_metrics_with_failure_class("success", None))

        r = json.loads(audit_path.read_text().strip())
        assert r["failure_class"] is None

    def test_on_task_start_audit(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        hooks = AuditHooks(audit_path=audit_path)
        hooks.on_task_start(UUID, _make_task())

        records = [json.loads(line) for line in audit_path.read_text().splitlines()]
        assert len(records) == 1
        r = records[0]
        assert r["event_type"] == "task_started"
        assert r["uuid"] == UUID
        assert r["status"] == "running"
        assert r["schema_version"] == 1
        assert "+09:00" in r["timestamp"]

    def test_on_task_start_no_audit_path_no_error(self):
        hooks = AuditHooks(audit_path=None)
        hooks.on_task_start(UUID, _make_task())

    def test_on_task_start_no_args_init_no_error(self):
        hooks = AuditHooks()
        hooks.on_task_start(UUID, _make_task())
