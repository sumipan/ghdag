"""on_task_start hook failures are isolated (issue sumipan/ghdag#8060)."""

from __future__ import annotations

import json
from pathlib import Path

from ghdag_dag_conftest_helpers import _run_engine_with_timeout

from ghdag.core.vocabulary import DONE_SUCCESS
from ghdag.dag.audit_hooks import AuditHooks
from ghdag.dag.engine import DagEngine
from ghdag.dag.models import DagConfig, Task


class _StartBoomHooks(AuditHooks):
    def on_task_start(self, uuid: str, task: Task) -> None:
        raise RuntimeError("start boom")


def _audit_records(audit_path: Path) -> list[dict]:
    if not audit_path.exists():
        return []
    return [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_start_hook_failure_is_audited_and_task_completes(tmp_path: Path) -> None:
    """Refs sumipan/ghdag#8060 AC8."""
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    done = jobs / "done"
    done.mkdir()
    result = jobs / "result.md"
    exec_jsonl = jobs / "exec.jsonl"
    exec_jsonl.write_text(
        json.dumps(
            {
                "uuid": "task-ok",
                "command": "echo ok",
                "depends": [],
                "result_path": str(result),
                "retry": 0,
                "idempotency_key": "corr-start",
                "annotations": {"_request_id": "req-start"},
            },
        )
        + "\n",
        encoding="utf-8",
    )
    config = DagConfig(
        exec_jsonl_path=exec_jsonl,
        exec_done_dir=done,
        poll_interval=0.05,
        launch_stagger=0.0,
    )
    engine = DagEngine(
        config,
        _StartBoomHooks(audit_path=jobs / "audit.jsonl", tz_name="UTC"),
    )
    _run_engine_with_timeout(engine, timeout=5.0)

    assert (done / "task-ok").read_text(encoding="utf-8").strip() == DONE_SUCCESS
    records = _audit_records(jobs / "audit.jsonl")
    types = [r["event_type"] for r in records]
    assert types.count("task_start_hook_failed") == 1
    assert types.count("task_complete") == 1
    failed = next(r for r in records if r["event_type"] == "task_start_hook_failed")
    assert failed["exception_type"] == "RuntimeError"
    assert failed["error_message"] == "start boom"
    assert failed["correlation_id"] == "corr-start"
    assert failed["request_id"] == "req-start"
    assert types.index("task_start_hook_failed") < types.index("task_complete")
