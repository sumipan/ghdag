"""Tests for ghdag.dag.engine — §5.4 acceptance criteria."""

from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from ghdag_dag_conftest_helpers import (
    _make_config,
)

from ghdag.core.models.metrics import TaskMetrics
from ghdag.io.audit import file_ts as audit_file_ts


class TestFanOutManagerUnit:
    """FanOutManager unit tests (acceptance criteria)."""



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


    def test_fanout_child_order_ts_uses_config_timezone(self, tmp_path):
        """Refs sumipan/nexus#6872."""
        from ghdag.dag.fanout import FanOutItem, FanOutSpec
        from ghdag.dag.fanout_manager import FanOutManager
        from ghdag.dag.hooks import DagHooks
        from ghdag.dag.models import Task

        queue = tmp_path / "jobs"
        queue.mkdir()
        config = _make_config(tmp_path, "", timezone="America/New_York")
        hooks = MagicMock(spec=DagHooks)
        fm = FanOutManager(
            config, hooks, lambda line, parent: None, lambda _: None
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
        fixed = datetime(2024, 7, 4, 16, 0, 0, tzinfo=ZoneInfo("UTC"))
        expected = audit_file_ts("America/New_York", now=fixed)

        def _file_ts(tz_name: str | None) -> str:
            return audit_file_ts(tz_name, now=fixed)

        child_uuid = f"{parent_uuid}--fo--a"
        with patch("ghdag.dag.fanout_manager.file_ts", side_effect=_file_ts):
            fm.spawn(parent_uuid, task, spec, metrics, queue_dir=queue)
        order_files = list(queue.glob(f"*-claude-order-{child_uuid}.md"))
        assert len(order_files) == 1
        assert order_files[0].name.startswith(expected)
