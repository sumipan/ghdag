"""Integration tests for ghdag run built-in recorder (issue sumipan/ghdag#8060)."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from ghdag.core.vocabulary import DONE_REJECTED, DONE_SUCCESS
from ghdag.dag.engine import DagEngine


def _write_exec(jobs_dir: Path, tasks: list[dict]) -> Path:
    exec_path = jobs_dir / "exec.jsonl"
    exec_path.write_text(
        "\n".join(json.dumps(t) for t in tasks) + "\n",
        encoding="utf-8",
    )
    return exec_path


def _write_silent_hooks(hooks_path: Path) -> None:
    hooks_path.write_text(
        "from ghdag.dag.hooks import DefaultHooks\n\n"
        "class SilentHooks(DefaultHooks):\n"
        "    pass\n\n"
        "HOOKS_CLASS = SilentHooks\n",
        encoding="utf-8",
    )


def _audit_records(audit_path: Path) -> list[dict]:
    if not audit_path.exists():
        return []
    return [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _exit_audit_count(audit_path: Path) -> int:
    exit_types = {
        "task_complete",
        "task_failed",
        "task_rejected",
        "task_empty_result",
    }
    return sum(1 for r in _audit_records(audit_path) if r.get("event_type") in exit_types)


def _metrics_by_uuid(metrics_path: Path) -> dict[str, dict]:
    if not metrics_path.exists():
        return {}
    out: dict[str, dict] = {}
    for line in metrics_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["uuid"]] = row
    return out


def _make_three_tasks(jobs_dir: Path) -> list[dict]:
    ok_result = jobs_dir / "ok.md"
    fail_result = jobs_dir / "fail.md"
    rej_result = jobs_dir / "rej.md"
    return [
        {
            "uuid": "task-ok",
            "engine": "shell",
            "command": f"echo ok > {ok_result}",
            "depends": [],
            "result_path": str(ok_result),
            "retry": 0,
            "idempotency_key": "idem-ok",
            "annotations": {"_request_id": "req-ok"},
        },
        {
            "uuid": "task-fail",
            "engine": "shell",
            "command": "exit 1",
            "depends": [],
            "result_path": str(fail_result),
            "retry": 0,
        },
        {
            "uuid": "task-rej",
            "engine": "shell",
            "command": (
                f"printf '%s\\n' '```ghdag' 'status: rejected' 'reason: retry' '```' > {rej_result}"
            ),
            "depends": [],
            "result_path": str(rej_result),
            "retry": 0,
        },
    ]


_TASK_UUIDS = ("task-ok", "task-fail", "task-rej")


def _run_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    recorder_enabled: bool,
) -> Path:
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    done_dir = jobs_dir / "done"
    done_dir.mkdir()
    quota = tmp_path / "quota-gate.json"
    _write_silent_hooks(tmp_path / "silent_hooks.py")
    exec_path = _write_exec(jobs_dir, _make_three_tasks(jobs_dir))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHDAG_RUN_RECORDER", raising=False)
    if recorder_enabled:
        monkeypatch.setenv("GHDAG_RUN_RECORDER", "1")
    else:
        monkeypatch.setenv("GHDAG_RUN_RECORDER", "0")

    engines: list[DagEngine] = []

    class _RecordingDagEngine(DagEngine):
        def __init__(self, config, hooks) -> None:
            super().__init__(config, hooks)
            engines.append(self)

    from ghdag.cli import main

    argv = [
        "run",
        str(exec_path),
        "--hooks",
        "silent_hooks",
        "--quota-state-path",
        str(quota),
        "--interval-sec",
        "0.05",
    ]

    def _run_main() -> None:
        with patch("ghdag.dag.engine.DagEngine", _RecordingDagEngine):
            main(argv)

    thread = threading.Thread(target=_run_main, daemon=True)
    thread.start()
    while thread.is_alive():
        if engines and all((done_dir / uid).exists() for uid in _TASK_UUIDS):
            engines[0]._shutdown = True
        thread.join(0.05)
    if engines:
        engines[0]._shutdown = True
    thread.join(5.0)
    return jobs_dir


def test_run_recorder_writes_audit_and_metrics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs sumipan/ghdag#8060 AC4."""
    jobs_dir = _run_cli(tmp_path, monkeypatch, recorder_enabled=True)
    done = jobs_dir / "done"
    audit_path = jobs_dir / "audit.jsonl"
    metrics_path = jobs_dir / "metrics.jsonl"

    assert (done / "task-ok").read_text(encoding="utf-8").strip() == DONE_SUCCESS
    assert (done / "task-fail").read_text(encoding="utf-8").strip() == "1"
    assert (done / "task-rej").read_text(encoding="utf-8").strip() == DONE_REJECTED

    by_type: dict[str, int] = {}
    for record in _audit_records(audit_path):
        et = record.get("event_type", "")
        by_type[et] = by_type.get(et, 0) + 1
    assert by_type.get("task_started") == 3
    assert by_type.get("task_complete") == 1
    assert by_type.get("task_failed") == 1
    assert by_type.get("task_rejected") == 1

    metrics = _metrics_by_uuid(metrics_path)
    assert set(metrics) == {"task-ok", "task-fail", "task-rej"}
    assert metrics["task-ok"]["correlation_id"] == "idem-ok"


def test_run_recorder_zero_preserves_custom_hook_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs sumipan/ghdag#8060 AC5."""
    jobs_dir = _run_cli(tmp_path, monkeypatch, recorder_enabled=False)
    done = jobs_dir / "done"
    audit_path = jobs_dir / "audit.jsonl"
    metrics_path = jobs_dir / "metrics.jsonl"

    assert (done / "task-ok").read_text(encoding="utf-8").strip() == DONE_SUCCESS
    assert (done / "task-fail").read_text(encoding="utf-8").strip() == "1"
    assert (done / "task-rej").read_text(encoding="utf-8").strip() == DONE_REJECTED
    assert _exit_audit_count(audit_path) == 0
    assert not metrics_path.exists()
