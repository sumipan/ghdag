"""Integration tests for DefaultHooks audit writing — Issue #960 AC-1 through AC-9."""

from __future__ import annotations

import json
import time


from ghdag.dag.hooks import DefaultHooks
from ghdag.dag.models import Task
from ghdag.metrics.models import TaskMetrics


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


class TestDefaultHooksAudit:
    def test_ac1_on_task_success(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        hooks = DefaultHooks(audit_path=audit_path)
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

    def test_ac2_on_task_failure(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        hooks = DefaultHooks(audit_path=audit_path)
        hooks.on_task_failure(UUID, _make_task(), 1, "error", _make_metrics("failure"))

        r = json.loads(audit_path.read_text().strip())
        assert r["event_type"] == "task_failed"
        assert r["status"] == "failure"

    def test_ac3_on_task_rejected(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        hooks = DefaultHooks(audit_path=audit_path)
        hooks.on_task_rejected(UUID, _make_task(), 0, False, _make_metrics("rejected"))

        r = json.loads(audit_path.read_text().strip())
        assert r["event_type"] == "task_rejected"
        assert r["status"] == "rejected"

    def test_ac4_on_task_dep_failed_all_none(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        hooks = DefaultHooks(audit_path=audit_path)
        hooks.on_task_dep_failed(UUID, _make_task(), "dep-uuid")

        r = json.loads(audit_path.read_text().strip())
        assert r["event_type"] == "task_dep_failed"
        assert r["status"] == "dep_failed"
        assert r["elapsed_sec"] is None
        assert r["token_count"] is None
        assert r["model"] is None
        assert r["engine"] is None

    def test_ac5_on_task_empty_result(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        hooks = DefaultHooks(audit_path=audit_path)
        hooks.on_task_empty_result(UUID, _make_task(), "", _make_metrics("empty_result"))

        r = json.loads(audit_path.read_text().strip())
        assert r["event_type"] == "task_empty_result"
        assert r["status"] == "empty_result"

    def test_ac6_no_audit_path_no_write(self, tmp_path):
        hooks = DefaultHooks(audit_path=None)
        task = _make_task()
        metrics = _make_metrics()
        # None of these should raise or write
        hooks.on_task_success(UUID, task, metrics)
        hooks.on_task_failure(UUID, task, 1, "err", metrics)
        hooks.on_task_rejected(UUID, task, 0, False, metrics)
        hooks.on_task_dep_failed(UUID, task, "dep")
        hooks.on_task_empty_result(UUID, task, "", metrics)
        # No file should exist
        assert not any(tmp_path.iterdir())

    def test_ac7_no_args_init_works(self):
        hooks = DefaultHooks()
        task = _make_task()
        metrics = _make_metrics()
        # Should not raise
        hooks.on_task_success(UUID, task, metrics)
        hooks.on_task_failure(UUID, task, 1, "err", metrics)
        hooks.on_task_rejected(UUID, task, 0, False, metrics)
        hooks.on_task_dep_failed(UUID, task, "dep")
        hooks.on_task_empty_result(UUID, task, "", metrics)
