"""AC-2: AuditHooks(tz_name=...) stamps audit timestamps in the given zone."""

from __future__ import annotations

import json
import time

from ghdag.dag.audit_hooks import AuditHooks
from ghdag.dag.models import Task
from ghdag.metrics.models import TaskMetrics

UUID = "tz-test-uuid-0000-0000-0000-000000000001"


def _make_task() -> Task:
    return Task(uuid=UUID, command="echo hello")


def _make_metrics() -> TaskMetrics:
    now = time.time()
    return TaskMetrics(
        uuid=UUID,
        engine="claude",
        model="claude-sonnet-4-6",
        wall_time_sec=1.0,
        token_count=10,
        status="success",
        started_at=now,
        finished_at=now + 1.0,
    )


def test_audit_hooks_asia_tokyo_timestamp(tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    hooks = AuditHooks(audit_path=audit_path, tz_name="Asia/Tokyo")
    hooks.on_task_success(UUID, _make_task(), _make_metrics())

    record = json.loads(audit_path.read_text(encoding="utf-8").strip())
    assert "+09:00" in record["timestamp"]


def test_audit_hooks_default_utc_timestamp(tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    hooks = AuditHooks(audit_path=audit_path)
    hooks.on_task_success(UUID, _make_task(), _make_metrics())

    record = json.loads(audit_path.read_text(encoding="utf-8").strip())
    assert "+00:00" in record["timestamp"]
