"""Convention test: round-trip correctness for QuotaGate, exec_jsonl, audit, and state_machine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path


def _utc(offset_seconds: float = 0) -> datetime:
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(seconds=offset_seconds)


def test_quota_gate_round_trip(tmp_path: Path) -> None:
    from ghdag.quota import QuotaGate

    state_path = tmp_path / "quota.json"
    audit_path = tmp_path / "audit.jsonl"
    gate = QuotaGate(state_path, audit_path=audit_path)

    t0 = _utc(0)
    t1 = _utc(3600)

    gate.report(engine="claude", status="paused", observed_at=t0, resume_at=t1)

    snap = gate.snapshot(now=t0)
    assert "claude" in snap.engines
    assert snap.engines["claude"].status == "paused"

    gate.defer("task-1", engine="claude", after=t0)

    released_before = gate.release_ready(now=_utc(1800))
    assert released_before == [], f"Expected no releases before resume_at, got {released_before}"

    released_after = gate.release_ready(now=_utc(7200))
    assert "task-1" in released_after, f"Expected task-1 released, got {released_after}"


def test_exec_jsonl_round_trip(tmp_path: Path) -> None:
    from ghdag.io import exec_jsonl
    from ghdag.io.audit import AuditContext

    path = tmp_path / "exec.jsonl"
    audit_path = tmp_path / "audit.jsonl"
    record = {"uuid": "uuid-round-trip-1", "command": "echo hello"}

    exec_jsonl.append(path, [record], AuditContext(source="test"), audit_path=audit_path)

    tasks = exec_jsonl.parse(path.read_text(encoding="utf-8"))
    assert len(tasks) >= 1
    matched = [t for t in tasks if t.uuid == "uuid-round-trip-1"]
    assert len(matched) == 1, f"uuid-round-trip-1 not found in parsed tasks: {tasks}"
    assert matched[0].command == "echo hello"


def test_audit_round_trip(tmp_path: Path) -> None:
    from ghdag.io.audit import write_task_exit_audit
    from ghdag.io.audit_query import read_task_exit_events

    audit_path = tmp_path / "audit.jsonl"

    write_task_exit_audit(
        audit_path,
        event_type="task_exit",
        uuid="u1",
        status="success",
    )

    events = read_task_exit_events(audit_path, uuid="u1")
    assert len(events) == 1, f"Expected 1 event, got {events}"
    assert events[0]["status"] == "success"
    assert events[0]["uuid"] == "u1"


def test_state_machine_round_trip(tmp_path: Path, monkeypatch: object) -> None:
    from ghdag.forge.local import LocalForge
    from ghdag.workflow import state_machine

    forge = LocalForge(tmp_path)
    issue_number = forge.issue_create("Test issue", "body", labels=["A"])

    monkeypatch.setenv("GHDAG_FORGE", "local")  # type: ignore[attr-defined]
    monkeypatch.setenv("GHDAG_FORGE_ROOT", str(tmp_path))  # type: ignore[attr-defined]

    transitions = {"A": ["B"], "B": []}
    state_machine.transition(issue_number, "B", transitions)

    updated = forge.issue_get(issue_number)
    label_names = [lbl["name"] for lbl in updated["labels"]]
    assert "B" in label_names, f"Expected B in labels, got {label_names}"
    assert "A" not in label_names, f"Expected A removed, got {label_names}"


def test_state_machine_invalid_transition_raises(tmp_path: Path, monkeypatch: object) -> None:
    import pytest

    from ghdag.forge.local import LocalForge
    from ghdag.workflow import state_machine

    forge = LocalForge(tmp_path)
    issue_number = forge.issue_create("Test issue", "body", labels=["A"])

    monkeypatch.setenv("GHDAG_FORGE", "local")  # type: ignore[attr-defined]
    monkeypatch.setenv("GHDAG_FORGE_ROOT", str(tmp_path))  # type: ignore[attr-defined]

    transitions = {"A": ["B"], "B": []}
    state_machine.transition(issue_number, "B", transitions)

    with pytest.raises(ValueError):
        state_machine.transition(issue_number, "A", transitions)

    after = forge.issue_get(issue_number)
    label_names = [lbl["name"] for lbl in after["labels"]]
    assert "B" in label_names, "Label B should remain after failed transition"
    assert "A" not in label_names, "Label A should not have been re-added"
