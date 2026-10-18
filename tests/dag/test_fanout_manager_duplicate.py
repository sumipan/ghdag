"""FanOutManager spawn when child uuids are already queued (Issue #5372)."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

from ghdag.dag.engine import DagEngine
from ghdag.dag.fanout import FanOutItem, FanOutSpec
from ghdag.dag.models import DagConfig, Task
from ghdag.metrics.models import TaskMetrics


def _base_metrics(uuid: str) -> TaskMetrics:
    t = time.time()
    return TaskMetrics(
        uuid=uuid,
        engine=None,
        model=None,
        wall_time_sec=1.0,
        token_count=None,
        status="success",
        started_at=t,
        finished_at=t,
    )


def _make_engine(tmp_path: Path) -> tuple[DagEngine, Path]:
    exec_file = tmp_path / "exec.jsonl"
    exec_file.write_text("")
    done_dir = tmp_path / "done"
    done_dir.mkdir()
    config = DagConfig(
        exec_jsonl_path=exec_file,
        exec_done_dir=done_dir,
        lock_file=tmp_path / ".lock",
    )
    engine = DagEngine(config, MagicMock())
    return engine, exec_file


class TestFanOutSpawnDuplicateChild:
    def test_respawn_does_not_raise_when_children_still_queued(self, tmp_path: Path) -> None:
        engine, exec_file = _make_engine(tmp_path)
        parent_uuid = "parent-dup"
        task = Task(uuid=parent_uuid, command="true")
        spec = FanOutSpec(
            children=[
                FanOutItem(id="c1", command="echo 1"),
                FanOutItem(id="c2", command="echo 2"),
            ]
        )
        metrics = _base_metrics(parent_uuid)

        engine._fanout_manager.spawn(parent_uuid, task, spec, metrics)
        first_line_count = len(
            [line for line in exec_file.read_text().splitlines() if line.strip()]
        )
        assert first_line_count == 2

        engine._fanout_manager.spawn(parent_uuid, task, spec, metrics)

        second_line_count = len(
            [line for line in exec_file.read_text().splitlines() if line.strip()]
        )
        assert second_line_count == first_line_count
        pending = engine._fanout_manager._pending[parent_uuid]
        assert pending == {
            f"{parent_uuid}--fo--c1",
            f"{parent_uuid}--fo--c2",
        }
