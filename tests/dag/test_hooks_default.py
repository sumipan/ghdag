"""Tests for DefaultHooks logging-only behavior — Issue #1090 AC-2, AC-3."""

from __future__ import annotations

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


class TestDefaultHooksLoggingOnly:
    def test_no_args_init(self):
        """AC-2: DefaultHooks() は引数なしでインスタンス化できる。"""
        hooks = DefaultHooks()
        assert hooks is not None

    def test_on_task_success_no_fs_write(self, tmp_path):
        """AC-3: on_task_success 呼び出し後、FS への書き込みが発生しない。"""
        hooks = DefaultHooks()
        hooks.on_task_success(UUID, _make_task(), _make_metrics("success"))
        assert not any(tmp_path.iterdir())

    def test_all_events_no_fs_write(self, tmp_path):
        """全イベントを呼び出してもファイルが作成されない。"""
        hooks = DefaultHooks()
        task = _make_task()
        metrics = _make_metrics()
        hooks.on_task_start(UUID, task)
        hooks.on_task_success(UUID, task, metrics)
        hooks.on_task_failure(UUID, task, 1, "err", metrics)
        hooks.on_task_rejected(UUID, task, 0, False, metrics)
        hooks.on_task_dep_failed(UUID, task, "dep")
        hooks.on_task_empty_result(UUID, task, "", metrics)
        assert not any(tmp_path.iterdir())
