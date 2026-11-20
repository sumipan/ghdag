"""Tests for ghdag.dag.engine — §5.4 acceptance criteria."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ghdag.core import engine_spec
from ghdag.dag.audit_hooks import AuditHooks
from ghdag.dag.engine import DagEngine
from ghdag.dag.models import DagConfig, Task
from ghdag.dag.task_launcher import TaskLauncher, _stderr_payload_bytes
from ghdag.llm.compaction import CompactionResult
from ghdag.quota import AdmissionDecision, QuotaGate


def _make_launcher(tmp_path: Path):
    from ghdag.dag.circuit_breaker import CircuitBreakerPolicy

    done = tmp_path / "jobs" / "done"
    done.mkdir(parents=True)
    config = DagConfig(
        exec_jsonl_path=tmp_path / "jobs" / "exec.jsonl",
        exec_done_dir=done,
    )
    hooks = MagicMock()
    hooks.check_rejected.return_value = False
    hooks.check_pipeline_status.return_value = None
    return TaskLauncher(
        config,
        hooks,
        CircuitBreakerPolicy(float("inf"), 2**31),
        MagicMock(),
        lambda _: None,
    )








@patch("ghdag.dag._process_host.subprocess.Popen")
def test_gemini_spawn_logs_unverified_warning_once(
    mock_popen: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Refs sumipan/nexus#6937 — TaskLauncher warns once per process for gemini."""
    proc = MagicMock()
    proc.pid = 1
    proc.stderr = None
    mock_popen.return_value = proc
    monkeypatch.setattr(engine_spec, "_WARNED_ENGINES", set())
    launcher = _make_launcher(tmp_path)
    task = Task(uuid="g1", command="gemini -p hi", engine="gemini")
    with caplog.at_level(logging.WARNING, logger="ghdag.core.engine_spec"):
        launcher._spawn("g1", task, task.command, "gemini", None)
        launcher._spawn("g2", task, task.command, "gemini", None)
    gemini_warnings = [
        r for r in caplog.records if r.levelno == logging.WARNING and "gemini" in r.message
    ]
    assert len(gemini_warnings) == 1


def test_popen_failure_restores_command_without_resume(tmp_path: Path) -> None:
    """Issue #6081: Popen failure rolls back injected resume command and quota reservation."""
    launcher = _make_launcher(tmp_path)
    launcher._session_store.record("parent-uuid", "claude", "sess-parent")
    original_command = "claude -p 'continue'"
    task = Task(
        uuid="child-uuid",
        command=original_command,
        engine="claude",
        annotations={"resume_from_uuid": "parent-uuid"},
    )
    skipped = CompactionResult(
        status="skipped",
        reason="below_threshold",
        session_id="sess-parent",
        parent_session_id="sess-parent",
        summary_tokens=None,
        tokens_before=10,
        tokens_after=10,
        compacted_key=None,
    )

    with patch(
        "ghdag.dag.task_launcher.compact_resume_session",
        return_value=skipped,
    ), patch(
        "ghdag.dag.task_launcher.write_compaction_audit",
    ), patch(
        "ghdag.dag._process_host.subprocess.Popen",
        side_effect=OSError("boom"),
    ):
        with pytest.raises(OSError):
            launcher.launch("child-uuid", task)

    assert task.command == original_command
    assert "_command_without_resume" not in task.annotations
    assert "resumed_session_id" not in task.annotations
    assert "child-uuid" not in launcher._quota_gate.snapshot().running_tasks




def _log_message_count(records: list[logging.LogRecord], needle: str) -> int:
    return sum(1 for record in records if needle in record.message)




def test_quota_deferred_log_is_emitted_once_for_same_reason(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Issue #6502 AC2: repeated quota denial for the same reason logs deferred once."""
    launcher = _make_launcher(tmp_path)
    task = Task(uuid="quota-uuid", command="claude -p ok", engine="claude")
    denied = AdmissionDecision(
        allowed=False,
        status="PAUSED",
        reason="budget: weekly cap",
        resume_at=None,
    )

    with patch.object(launcher._quota_gate, "begin_run", return_value=denied), caplog.at_level(
        logging.INFO, logger="ghdag.dag.task_launcher"
    ):
        assert launcher.launch("quota-uuid", task) is False
        assert launcher.launch("quota-uuid", task) is False

    assert _log_message_count(caplog.records, "launch deferred by quota gate") == 1








# --- Failure stderr artifacts ---


def _run_engine_to_completion(
    engine: DagEngine,
    *,
    done_dir: Path,
    task_uuids: tuple[str, ...],
) -> None:
    """Run engine.run() on a daemon thread until listed tasks have done markers."""
    thread = threading.Thread(target=engine.run, daemon=True)
    thread.start()
    while thread.is_alive():
        if all((done_dir / uid).exists() for uid in task_uuids):
            engine._shutdown = True
        thread.join(0.05)
    engine._shutdown = True
    thread.join()


def test_task_completion_records_claude_quota_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs sumipan/nexus#6717 — task stdout rate_limit_event is stored on quota gate."""
    import os
    import stat

    fixture = (
        Path(__file__).resolve().parent.parent
        / "fixtures"
        / "claude_stream_rate_limit_event.jsonl"
    ).read_text(encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    claude = bin_dir / "claude"
    claude.write_text(
        f"""#!/bin/sh
cat <<'EOF'
{fixture}
EOF
""",
        encoding="utf-8",
    )
    claude.chmod(claude.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    uuid = "quota-usage-uuid"
    jobs = tmp_path / "jobs"
    done = jobs / "done"
    done.mkdir(parents=True)
    quota_path = jobs / "quota-gate.json"
    exec_jsonl = tmp_path / "exec.jsonl"
    exec_jsonl.write_text(
        json.dumps(
            {
                "uuid": uuid,
                "command": "claude -p hi",
                "depends": [],
                "engine": "claude",
                "result_path": str(tmp_path / "result.md"),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    config = DagConfig(
        exec_jsonl_path=str(exec_jsonl),
        exec_done_dir=str(done),
        poll_interval=0.05,
        launch_stagger=0.0,
        lock_file=str(tmp_path / "lock"),
        quota_state_path=str(quota_path),
        quota_audit_path=str(jobs / "quota-audit.jsonl"),
    )
    engine = DagEngine(config, AuditHooks(audit_path=jobs / "audit.jsonl"))
    _run_engine_to_completion(engine, done_dir=done, task_uuids=(uuid,))
    gate = QuotaGate(quota_path)
    usage = gate.snapshot().usage.get("claude")
    assert usage is not None
    assert any(w.window_minutes == 300 and w.used_percent == 34.0 for w in usage.windows)










def test_persisted_stderr_keeps_one_mib_tail() -> None:
    """Oversized stderr keeps a <=1 MiB tail with an end marker."""
    marker = "TAIL_MARKER_END"
    prefix = "x" * 1_048_576
    payload = _stderr_payload_bytes(prefix + marker)
    assert len(payload) <= 1_048_576
    assert payload.endswith(marker.encode("utf-8"))


def test_state_paths_use_done_parent(tmp_path: Path) -> None:
    """Issue #7548 AC1 — control paths share exec_done_dir's parent as state root."""
    from ghdag.dag.circuit_breaker import CircuitBreakerPolicy

    state_root = tmp_path / "state"
    done = state_root / "done"
    done.mkdir(parents=True)
    config = DagConfig(
        exec_jsonl_path=state_root / "exec.jsonl",
        exec_done_dir=done,
    )
    hooks = MagicMock()
    launcher = TaskLauncher(
        config,
        hooks,
        CircuitBreakerPolicy(float("inf"), 2**31),
        MagicMock(),
        lambda _: None,
    )
    resolved = state_root.resolve()
    uuid = "state-path-check"

    assert launcher._state_dir().resolve() == resolved
    assert launcher._session_store._store_dir.resolve().parent == resolved
    assert launcher._quota_gate._state_path.resolve().parent == resolved
    assert launcher._queue_audit_path().resolve().parent == resolved
    assert launcher._running_path(uuid).parent.parent == resolved
    assert launcher._quota_waiting_path(uuid).parent.parent == resolved
    assert launcher._cancel_path(uuid).parent.parent == resolved
    assert launcher._events_path(uuid).parent.parent == resolved




