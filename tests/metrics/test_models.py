"""Tests for TaskMetrics — Issue #961 AC-4."""

from __future__ import annotations

import time

import pytest

from ghdag.metrics.models import TaskMetrics


UUID = "test-uuid-0000-0000-0000-000000000001"


class TestTaskMetrics:
    def test_ac4_backward_compatible_construction(self):
        """AC-4: 既存引数のみでの構築が成功し、correlation_id が None である。"""
        now = time.time()
        m = TaskMetrics(
            uuid=UUID,
            engine="claude",
            model="claude-sonnet-4-6",
            wall_time_sec=10.5,
            token_count=1500,
            status="success",
            started_at=now,
            finished_at=now + 10.5,
        )
        assert m.correlation_id is None

    def test_ac4_with_correlation_id(self):
        """AC-4: correlation_id を渡した場合にフィールドに格納される。"""
        now = time.time()
        m = TaskMetrics(
            uuid=UUID,
            engine="claude",
            model="claude-sonnet-4-6",
            wall_time_sec=10.5,
            token_count=1500,
            status="success",
            started_at=now,
            finished_at=now + 10.5,
            correlation_id="issuesmith:brushup:958",
        )
        assert m.correlation_id == "issuesmith:brushup:958"

    def test_ac4_frozen(self):
        """frozen=True なので変更不可。"""
        now = time.time()
        m = TaskMetrics(
            uuid=UUID, engine=None, model=None,
            wall_time_sec=1.0, token_count=None, status="success",
            started_at=now, finished_at=now + 1.0,
        )
        with pytest.raises(Exception):
            m.correlation_id = "should-fail"  # type: ignore[misc]
