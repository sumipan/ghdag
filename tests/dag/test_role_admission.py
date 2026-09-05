"""Role-based quota admission, deferred propagation, and TTL override (Issue #2871)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ghdag.core.models.workflow import (
    HandlerConfig,
    StepConfig,
    WorkflowConfig,
    validate_workflow_roles,
)
from ghdag.dag.engine import DagEngine
from ghdag.dag.models import DagConfig
from ghdag.io import exec_jsonl
from ghdag.io.audit import AuditContext
from ghdag.quota import QuotaGate

JST = timezone(timedelta(hours=9))


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 5, hour, minute, tzinfo=JST)


def _write_exec(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


class TestRoleAdmission:
    def test_admit_receives_role_and_role_engines(self, tmp_path: Path) -> None:
        gate = QuotaGate(tmp_path / "quota-gate.json")
        gate.report(engine="claude", status="paused", observed_at=_dt(12))
        gate.report(engine="codex", status="paused", observed_at=_dt(12))

        decision = gate.admit(
            task_uuid="task-1",
            engine="claude",
            phase="launch",
            role="design",
            role_engines=["claude", "codex"],
            now=_dt(12, 30),
        )
        assert decision.allowed is False
        assert decision.status == "DEFERRED"

        deferred = gate.snapshot().deferred_tasks["task-1"]
        assert deferred.role == "design"

    def test_any_role_engine_available_allows_admission(self, tmp_path: Path) -> None:
        gate = QuotaGate(tmp_path / "quota-gate.json")
        gate.report(engine="claude", status="paused", observed_at=_dt(12))
        gate.clear(engine="codex", observed_at=_dt(12))

        decision = gate.admit(
            task_uuid="task-1",
            engine="claude",
            phase="launch",
            role="design",
            role_engines=["claude", "codex"],
            now=_dt(12, 30),
        )
        assert decision.allowed is True
        assert decision.status == "ALLOWED"

    def test_clear_releases_role_deferred_task(self, tmp_path: Path) -> None:
        gate = QuotaGate(tmp_path / "quota-gate.json")
        gate.report(engine="claude", status="paused", observed_at=_dt(12))
        gate.report(engine="codex", status="paused", observed_at=_dt(12))
        gate.admit(
            task_uuid="task-1",
            engine="claude",
            phase="launch",
            role="design",
            role_engines=["claude", "codex"],
            now=_dt(12, 5),
        )
        assert "task-1" in gate.snapshot().deferred_tasks

        gate.clear(engine="claude", observed_at=_dt(12, 10))
        assert "task-1" not in gate.snapshot().deferred_tasks

    def test_clear_ttl_blocks_pause_report_until_expiry(self, tmp_path: Path) -> None:
        gate = QuotaGate(tmp_path / "quota-gate.json")
        gate.report(engine="claude", status="paused", observed_at=_dt(12))

        gate.clear(engine="claude", observed_at=_dt(12, 1), ttl_seconds=600)

        blocked = gate.report(engine="claude", status="paused", observed_at=_dt(12, 5))
        assert blocked.applied is False
        snapshot = gate.snapshot(now=_dt(12, 5))
        assert "claude" not in snapshot.engines or snapshot.engines["claude"].status != "paused"

        applied = gate.report(engine="claude", status="paused", observed_at=_dt(12, 12))
        assert applied.applied is True
        assert gate.snapshot(now=_dt(12, 12)).engines["claude"].status == "paused"

    def test_role_unspecified_preserves_engine_admission(self, tmp_path: Path) -> None:
        gate = QuotaGate(tmp_path / "quota-gate.json")
        gate.clear(engine="claude", observed_at=_dt(12))
        decision = gate.admit(
            task_uuid="task-1",
            engine="claude",
            phase="launch",
            now=_dt(12, 1),
        )
        assert decision.allowed is True


class TestWorkflowRoleValidation:
    def test_unknown_role_raises_validation_error(self) -> None:
        config = WorkflowConfig(
            name="wf",
            triggers=[],
            handlers={
                "h": HandlerConfig(
                    steps=[
                        StepConfig(
                            id="cp2",
                            template="t",
                            model="m",
                            role="design",
                        )
                    ]
                )
            },
            roles={},
        )
        with pytest.raises(ValueError, match="design"):
            validate_workflow_roles(config)

    def test_declared_role_passes_validation(self) -> None:
        config = WorkflowConfig(
            name="wf",
            triggers=[],
            handlers={
                "h": HandlerConfig(
                    steps=[
                        StepConfig(
                            id="cp2",
                            template="t",
                            model="m",
                            role="design",
                        )
                    ]
                )
            },
            roles={"design": ["claude", "codex"]},
        )
        validate_workflow_roles(config)


class TestRoleDeferredDagPropagation:
    def test_dependent_waits_when_parent_deferred_by_role(self, tmp_path: Path) -> None:
        exec_path = tmp_path / "jobs" / "exec.jsonl"
        done_dir = tmp_path / "jobs" / "done"
        done_dir.mkdir(parents=True, exist_ok=True)
        _write_exec(
            exec_path,
            [
                {
                    "uuid": "parent",
                    "command": "claude -p hello",
                    "engine": "claude",
                    "depends": [],
                    "annotations": {
                        "role": "design",
                        "role_engines": ["claude", "codex"],
                    },
                },
                {
                    "uuid": "child",
                    "command": "echo child",
                    "engine": "shell",
                    "depends": ["parent"],
                },
            ],
        )

        config = DagConfig(exec_jsonl_path=exec_path, exec_done_dir=done_dir, poll_interval=0.01)
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        hooks.check_pipeline_status.return_value = None
        engine = DagEngine(config, hooks)
        engine._quota_gate.report(engine="claude", status="paused", observed_at=_dt(12))
        engine._quota_gate.report(engine="codex", status="paused", observed_at=_dt(12))

        def stop_after_first_sleep(*_args, **_kwargs):
            engine._shutdown = True

        with patch("ghdag.dag.task_launcher.subprocess.Popen") as mock_popen, patch(
            "ghdag.dag.engine.time.sleep", side_effect=stop_after_first_sleep
        ):
            engine.run()

        mock_popen.assert_not_called()
        assert "parent" in engine._quota_gate.snapshot().deferred_tasks
        assert not (done_dir / "child").exists()
        hooks.on_task_dep_failed.assert_not_called()

    def test_enqueue_passes_role_to_admit(self, tmp_path: Path) -> None:
        exec_path = tmp_path / "jobs" / "exec.jsonl"
        gate = QuotaGate(tmp_path / "quota-gate.json")
        gate.report(engine="claude", status="paused", observed_at=_dt(12))
        gate.report(engine="codex", status="paused", observed_at=_dt(12))

        exec_jsonl.append(
            exec_path,
            [
                {
                    "uuid": "task-1",
                    "command": "claude -p hi",
                    "engine": "claude",
                    "depends": [],
                    "annotations": {
                        "role": "design",
                        "role_engines": ["claude", "codex"],
                    },
                }
            ],
            AuditContext(source="test"),
            audit_path=tmp_path / "audit.jsonl",
            quota_gate=gate,
        )

        deferred = gate.snapshot().deferred_tasks["task-1"]
        assert deferred.role == "design"
