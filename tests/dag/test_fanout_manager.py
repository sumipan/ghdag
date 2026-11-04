"""Tests for ghdag.dag.engine — §5.4 acceptance criteria."""

import time
from unittest.mock import MagicMock

from ghdag_dag_conftest_helpers import (
    MemoryHooks,
    _make_config,
)

from ghdag.metrics.models import TaskMetrics


class TestFanOutManagerUnit:
    """FanOutManager unit tests (acceptance criteria)."""

    def test_spawn_calls_append_task_fn_and_registers_pending(self, tmp_path):

        from ghdag.dag.fanout import FanOutItem, FanOutSpec
        from ghdag.dag.fanout_manager import FanOutManager
        from ghdag.dag.hooks import DagHooks
        from ghdag.dag.models import Task
        from ghdag.metrics.models import TaskMetrics

        config = _make_config(tmp_path, "")
        hooks = MagicMock(spec=DagHooks)
        appended: list[tuple[str, str]] = []
        fm = FanOutManager(
            config, hooks, lambda line, parent: appended.append((line, parent)), lambda _: None
        )

        parent_uuid = "parent"
        task = Task(uuid=parent_uuid, command="true")
        spec = FanOutSpec(children=[
            FanOutItem(id="c1", command="echo 1"),
            FanOutItem(id="c2", command="echo 2"),
        ])
        t = time.time()
        metrics = TaskMetrics(
            uuid=parent_uuid, engine=None, model=None,
            wall_time_sec=1.0, token_count=None, status="success",
            started_at=t, finished_at=t,
        )
        fm.spawn(parent_uuid, task, spec, metrics)

        assert len(appended) == 2
        assert all(parent == parent_uuid for _, parent in appended)
        assert fm.is_pending(parent_uuid)

    def test_check_completions_marks_parent_done_when_all_children_complete(self, tmp_path):

        from ghdag.dag.fanout_manager import FanOutManager
        from ghdag.dag.models import Task

        config = _make_config(tmp_path, "")
        # Create done dir so state.mark_done can write there
        import os
        os.makedirs(str(tmp_path / "jobs" / "done"), exist_ok=True)

        hooks = MemoryHooks()
        fm = FanOutManager(config, hooks, lambda _line, _parent: None, lambda _: None)

        parent_uuid = "parent-fm"
        child_uuids = {f"{parent_uuid}--fo--c1", f"{parent_uuid}--fo--c2"}
        task = Task(uuid=parent_uuid, command="true")
        t = time.time()
        metrics = TaskMetrics(
            uuid=parent_uuid, engine=None, model=None,
            wall_time_sec=1.0, token_count=None, status="success",
            started_at=t, finished_at=t,
        )
        fm._pending[parent_uuid] = set(child_uuids)
        fm._tasks[parent_uuid] = task
        fm._metrics[parent_uuid] = metrics

        known_done = set(child_uuids)
        known_succeeded = set(child_uuids)
        fm.check_completions(known_done, known_succeeded)

        assert parent_uuid in known_done
        assert not fm.is_pending(parent_uuid)
        assert len(hooks.successes) == 1
