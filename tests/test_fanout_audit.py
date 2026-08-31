"""Fan-out child append must write audit.jsonl (nexus Issue #2674)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

from ghdag.dag.engine import DagEngine
from ghdag.dag.fanout import FanOutItem, FanOutSpec
from ghdag.dag.hooks import DagHooks
from ghdag.dag.models import DagConfig, Task
from ghdag.io.audit import AuditContext
from ghdag.metrics.models import TaskMetrics
from ghdag.pipeline.state import PipelineState


def _make_config(tmp_path: Path) -> DagConfig:
    jobs = tmp_path / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    (jobs / "done").mkdir()
    exec_path = jobs / "exec.jsonl"
    exec_path.write_text("", encoding="utf-8")
    return DagConfig(
        exec_jsonl_path=str(exec_path),
        exec_done_dir=str(jobs / "done"),
        poll_interval=0.01,
        launch_stagger=0.0,
    )


class TestFanOutAudit:
    def test_fanout_spawn_writes_audit_with_source_fanout(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        hooks = MagicMock(spec=DagHooks)
        engine = DagEngine(config, hooks=hooks)

        parent_uuid = "parent-audit"
        task = Task(uuid=parent_uuid, command="true")
        spec = FanOutSpec(children=[
            FanOutItem(id="c1", command="echo 1"),
            FanOutItem(id="c2", command="echo 2"),
        ])
        t = time.time()
        metrics = TaskMetrics(
            uuid=parent_uuid,
            engine=None,
            model=None,
            wall_time_sec=1.0,
            token_count=None,
            status="success",
            started_at=t,
            finished_at=t,
        )

        engine._fanout_manager.spawn(parent_uuid, task, spec, metrics)

        exec_path = Path(config.exec_jsonl_path)
        exec_lines = [ln for ln in exec_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(exec_lines) == 2

        audit_path = exec_path.parent / "audit.jsonl"
        assert audit_path.exists()
        audit_records = [
            json.loads(ln) for ln in audit_path.read_text(encoding="utf-8").splitlines() if ln.strip()
        ]
        # 子タスクごとに 1 件（append 1 record ずつ）→ 2 件、いずれも source=fanout
        assert len(audit_records) == 2
        for rec in audit_records:
            assert rec["source"] == "fanout"
            assert rec["correlation_id"] == parent_uuid
            assert rec["exec_lines_count"] == 1
            assert len(rec["task_uuids"]) == 1

    def test_pipeline_append_audit_unchanged_no_duplicate(self, tmp_path: Path) -> None:
        """PipelineState.append_exec_records 経路の audit は従来どおり 1 件のみ。"""
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        exec_path = tmp_path / "jobs" / "exec.jsonl"
        exec_path.parent.mkdir()
        state = PipelineState(state_dir, exec_path)

        records = [{"uuid": "p1", "command": "echo"}]
        state.append_exec_records(
            records,
            audit_context=AuditContext(source="issuesmith", correlation_id="corr-1"),
        )

        audit_path = exec_path.parent / "audit.jsonl"
        lines = [ln for ln in audit_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["source"] == "issuesmith"
        assert rec["correlation_id"] == "corr-1"
        assert rec["task_uuids"] == ["p1"]
        assert rec["exec_lines_count"] == 1
