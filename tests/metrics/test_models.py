"""Tests for TaskMetrics — Issue #961 AC-4, Issue #1041."""

from __future__ import annotations

import time

import pytest

from ghdag.metrics.models import FailureClass, TaskMetrics


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

    # --- Issue #962 tests ---

    def test_failure_class_default_none(self):
        """failure_class 未指定 → None（後方互換）。"""
        now = time.time()
        m = TaskMetrics(
            uuid=UUID, engine=None, model=None,
            wall_time_sec=1.0, token_count=None, status="failure",
            started_at=now, finished_at=now + 1.0,
        )
        assert m.failure_class is None

    def test_failure_class_set(self):
        """failure_class=FailureClass.TIMEOUT を渡すとフィールドに格納される。"""
        now = time.time()
        m = TaskMetrics(
            uuid=UUID, engine=None, model=None,
            wall_time_sec=1.0, token_count=None, status="failure",
            started_at=now, finished_at=now + 1.0,
            failure_class=FailureClass.TIMEOUT,
        )
        assert m.failure_class == FailureClass.TIMEOUT
        assert m.failure_class.value == "TIMEOUT"


# --- Issue #1041 tests ---


class TestFailureClass:
    def test_all_9_values_exist(self):
        """FailureClass enum が 9 値を持つ。"""
        expected = {
            "TIMEOUT", "REJECTED", "PROCESS_ERROR", "PIPELINE_FAILED",
            "EMPTY_RESULT", "FANOUT_CHILD_FAILED", "FANOUT_PARSE_FAILED",
            "DEP_FAILED", "UNKNOWN_FAILURE",
        }
        assert {fc.value for fc in FailureClass} == expected

    def test_timeout_meta(self):
        assert FailureClass.TIMEOUT.value == "TIMEOUT"
        assert FailureClass.TIMEOUT.cause == "transient"
        assert FailureClass.TIMEOUT.retry_policy == "safe"

    def test_rejected_meta(self):
        assert FailureClass.REJECTED.value == "REJECTED"
        assert FailureClass.REJECTED.cause == "permanent"
        assert FailureClass.REJECTED.retry_policy == "forbidden"

    def test_process_error_meta(self):
        assert FailureClass.PROCESS_ERROR.value == "PROCESS_ERROR"
        assert FailureClass.PROCESS_ERROR.cause == "permanent"
        assert FailureClass.PROCESS_ERROR.retry_policy == "requires_review"

    def test_pipeline_failed_meta(self):
        assert FailureClass.PIPELINE_FAILED.value == "PIPELINE_FAILED"
        assert FailureClass.PIPELINE_FAILED.cause == "permanent"
        assert FailureClass.PIPELINE_FAILED.retry_policy == "requires_review"

    def test_empty_result_meta(self):
        assert FailureClass.EMPTY_RESULT.value == "EMPTY_RESULT"
        assert FailureClass.EMPTY_RESULT.cause == "unknown"
        assert FailureClass.EMPTY_RESULT.retry_policy == "requires_review"

    def test_fanout_child_failed_meta(self):
        assert FailureClass.FANOUT_CHILD_FAILED.value == "FANOUT_CHILD_FAILED"
        assert FailureClass.FANOUT_CHILD_FAILED.cause == "permanent"
        assert FailureClass.FANOUT_CHILD_FAILED.retry_policy == "forbidden"

    def test_fanout_parse_failed_meta(self):
        assert FailureClass.FANOUT_PARSE_FAILED.value == "FANOUT_PARSE_FAILED"
        assert FailureClass.FANOUT_PARSE_FAILED.cause == "permanent"
        assert FailureClass.FANOUT_PARSE_FAILED.retry_policy == "requires_review"

    def test_dep_failed_meta(self):
        assert FailureClass.DEP_FAILED.value == "DEP_FAILED"
        assert FailureClass.DEP_FAILED.cause == "permanent"
        assert FailureClass.DEP_FAILED.retry_policy == "forbidden"

    def test_unknown_failure_meta(self):
        assert FailureClass.UNKNOWN_FAILURE.value == "UNKNOWN_FAILURE"
        assert FailureClass.UNKNOWN_FAILURE.cause == "unknown"
        assert FailureClass.UNKNOWN_FAILURE.retry_policy == "requires_review"

    def test_invalid_value_raises(self):
        """不正な文字列からの生成を拒否する。"""
        with pytest.raises(ValueError):
            FailureClass("INVALID")

    def test_task_metrics_with_enum(self):
        """TaskMetrics(failure_class=FailureClass.TIMEOUT) で生成できる。"""
        now = time.time()
        m = TaskMetrics(
            uuid=UUID, engine=None, model=None,
            wall_time_sec=1.0, token_count=None, status="failure",
            started_at=now, finished_at=now + 1.0,
            failure_class=FailureClass.TIMEOUT,
        )
        assert m.failure_class == FailureClass.TIMEOUT
        assert m.failure_class.value == "TIMEOUT"

    def test_task_metrics_default_none(self):
        """TaskMetrics() のデフォルト failure_class は None。"""
        now = time.time()
        m = TaskMetrics(
            uuid=UUID, engine=None, model=None,
            wall_time_sec=1.0, token_count=None, status="success",
            started_at=now, finished_at=now + 1.0,
        )
        assert m.failure_class is None
