"""Tests for ghdag.dag.engine — §5.4 acceptance criteria."""

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
        t = 1_700_000_000.0
        metrics = TaskMetrics(
            uuid=parent_uuid, engine=None, model=None,
            wall_time_sec=1.0, token_count=None, status="success",
            started_at=t, finished_at=t,
        )
        fm.spawn(parent_uuid, task, spec, metrics)

        assert len(appended) == 2
        assert all(parent == parent_uuid for _, parent in appended)
        assert fm.is_pending(parent_uuid)

    def test_spawn_prompt_child_writes_order_and_exec_record(self, tmp_path):
        import json

        from ghdag.dag.fanout import FanOutItem, FanOutSpec
        from ghdag.dag.fanout_manager import FanOutManager
        from ghdag.dag.hooks import DagHooks
        from ghdag.dag.models import Task

        queue = tmp_path / "jobs"
        queue.mkdir()
        config = _make_config(tmp_path, "")
        hooks = MagicMock(spec=DagHooks)
        appended: list[str] = []
        fm = FanOutManager(
            config, hooks, lambda line, parent: appended.append(line), lambda _: None
        )
        parent_uuid = "parent"
        task = Task(
            uuid=parent_uuid,
            command="claude -p < order.md",
            engine="claude",
            model="claude-sonnet-4-6",
        )
        spec = FanOutSpec(children=[FanOutItem(id="a", prompt="do a")])
        t = 1_700_000_000.0
        metrics = TaskMetrics(
            uuid=parent_uuid,
            engine="claude",
            model="claude-sonnet-4-6",
            wall_time_sec=1.0,
            token_count=None,
            status="success",
            started_at=t,
            finished_at=t,
        )
        fm.spawn(parent_uuid, task, spec, metrics, queue_dir=queue)

        assert len(appended) == 1
        record = json.loads(appended[0])
        child_uuid = f"{parent_uuid}--fo--a"
        assert record["uuid"] == child_uuid
        assert record["engine"] == "claude"
        assert record["model"] == "claude-sonnet-4-6"
        assert record["result_path"].endswith(f"-result-{child_uuid}.md")
        assert record["annotations"]["fanout_parent"] == parent_uuid
        assert record["annotations"]["step_name"] == "a"
        assert "--resume" not in record["command"]
        order_files = list(queue.glob(f"*-claude-order-{child_uuid}.md"))
        assert len(order_files) == 1
        assert order_files[0].read_text(encoding="utf-8") == "do a"

    def test_cursor_child_does_not_inherit_parent_model(self, tmp_path):
        import json

        from ghdag.dag.fanout import FanOutItem, FanOutSpec
        from ghdag.dag.fanout_manager import FanOutManager
        from ghdag.dag.hooks import DagHooks
        from ghdag.dag.models import Task

        queue = tmp_path / "jobs"
        queue.mkdir()
        config = _make_config(tmp_path, "")
        parent_uuid = "parent"
        task = Task(
            uuid=parent_uuid,
            command="true",
            engine="claude",
            model="claude-sonnet-4-6",
        )
        spec = FanOutSpec(children=[FanOutItem(id="a", prompt="x", engine="cursor")])
        metrics = TaskMetrics(
            uuid=parent_uuid,
            engine="claude",
            model="claude-sonnet-4-6",
            wall_time_sec=1.0,
            token_count=None,
            status="success",
            started_at=1_700_000_000.0,
            finished_at=1_700_000_000.0,
        )
        lines: list[str] = []
        fm2 = FanOutManager(
            config,
            MagicMock(spec=DagHooks),
            lambda line, _p: lines.append(line),
            lambda _: None,
        )
        fm2.spawn(parent_uuid, task, spec, metrics, queue_dir=queue)
        record = json.loads(lines[0])
        assert record["engine"] == "cursor"
        assert record["model"] is None

    def test_invalid_later_child_does_not_append_earlier_sibling(self, tmp_path):
        import pytest

        from ghdag.dag.fanout import FanoutError, FanOutItem, FanOutSpec
        from ghdag.dag.fanout_manager import FanOutManager
        from ghdag.dag.hooks import DagHooks
        from ghdag.dag.models import Task

        config = _make_config(tmp_path, "")
        appended: list[str] = []
        fm = FanOutManager(
            config,
            MagicMock(spec=DagHooks),
            lambda line, _parent: appended.append(line),
            lambda _: None,
        )
        task = Task(uuid="parent", command="true", engine="claude")
        spec = FanOutSpec(
            children=[
                FanOutItem(id="a", command="echo a"),
                FanOutItem(
                    id="b",
                    prompt="do b",
                    engine="claude",
                    model="definitely-not-supported",
                ),
            ]
        )
        metrics = TaskMetrics(
            uuid="parent",
            engine="claude",
            model=None,
            wall_time_sec=1.0,
            token_count=None,
            status="success",
            started_at=1_700_000_000.0,
            finished_at=1_700_000_000.0,
        )

        with pytest.raises(FanoutError):
            fm.spawn("parent", task, spec, metrics, queue_dir=tmp_path / "jobs")

        assert appended == []
        assert not fm.is_pending("parent")

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
        t = 1_700_000_000.0
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
