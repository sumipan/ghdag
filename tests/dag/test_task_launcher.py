"""Tests for ghdag.dag.engine — §5.4 acceptance criteria."""

from __future__ import annotations

import io
import json
import logging
import subprocess
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from ghdag_dag_conftest_helpers import MemoryHooks, _make_config

from ghdag.core.capabilities import WEB_RESEARCH
from ghdag.core.command import render_exec_command
from ghdag.core.engine_spec import ENGINE_SPECS
from ghdag.core.vocabulary import DONE_SKIPPED_MISSING_INPUT
from ghdag.dag.audit_hooks import AuditHooks
from ghdag.dag.engine import DagEngine
from ghdag.dag.models import DagConfig, Task, _RunningProcess
from ghdag.dag.task_launcher import TaskLauncher, _stderr_payload_bytes
from ghdag.llm.compaction import CompactionResult
from ghdag.quota import AdmissionDecision, QuotaGate


class TestTaskLauncherUnit:
    """TaskLauncher unit tests: launch() starts a process, check_completions() detects done."""

    def test_launch_registers_running_task(self, tmp_path):

        from ghdag.dag.circuit_breaker import CircuitBreakerPolicy
        from ghdag.dag.hooks import DagHooks
        from ghdag.dag.models import Task
        from ghdag.dag.task_launcher import TaskLauncher

        config = _make_config(tmp_path, "")
        hooks = MagicMock(spec=DagHooks)
        hooks.check_rejected.return_value = False
        hooks.check_pipeline_status.return_value = None
        cb = CircuitBreakerPolicy(float("inf"), 2**31)
        fm = MagicMock()
        launcher = TaskLauncher(config, hooks, cb, fm, lambda _: None)

        task = Task(uuid="launch-test", command="true")
        launcher.launch("launch-test", task)

        assert launcher.is_running("launch-test")
        assert launcher.running_count == 1

        # wait for completion
        launcher._running["launch-test"].proc.wait()
        launcher.check_completions()
        assert not launcher.is_running("launch-test")
        assert launcher.running_count == 0

    def test_launch_ignores_legacy_role_annotations(self, tmp_path: Path) -> None:
        """Refs sumipan/nexus#6680."""
        from ghdag.dag.circuit_breaker import CircuitBreakerPolicy
        from ghdag.dag.hooks import DagHooks
        from ghdag.dag.models import Task

        done = tmp_path / "jobs" / "done"
        done.mkdir(parents=True)
        config = DagConfig(
            exec_jsonl_path=tmp_path / "jobs" / "exec.jsonl",
            exec_done_dir=done,
            quota_state_path=tmp_path / "quota-gate.json",
        )
        hooks = MagicMock(spec=DagHooks)
        hooks.check_rejected.return_value = False
        hooks.check_pipeline_status.return_value = None
        launcher = TaskLauncher(
            config,
            hooks,
            CircuitBreakerPolicy(float("inf"), 2**31),
            MagicMock(),
            lambda _: None,
        )
        engines_key = "role" + "_engines"
        task = Task(
            uuid="legacy-role-task",
            command="claude -p hi",
            engine="claude",
            annotations={
                "role": "impl",
                engines_key: ["cursor", "claude"],
            },
        )
        dummy_proc = MagicMock()
        dummy_proc.stdout = None
        dummy_proc.stderr = io.BytesIO(b"")
        dummy_proc.poll.return_value = None
        dummy_proc.pid = 5150

        with patch("ghdag.dag.task_launcher.subprocess.Popen", return_value=dummy_proc):
            assert launcher.launch("legacy-role-task", task) is True

        running = launcher._quota_gate.read_state()["running_tasks"]["legacy-role-task"]
        assert running["engine"] == "claude"
        assert "role" not in running
        launcher._quota_gate.finish_run(task_uuid="legacy-role-task")

    def test_check_completions_detects_completed_process(self, tmp_path):
        import io
        import subprocess

        from ghdag.dag.circuit_breaker import CircuitBreakerPolicy
        from ghdag.dag.models import Task, _RunningProcess
        from ghdag.dag.task_launcher import TaskLauncher

        config = _make_config(tmp_path, "")
        hooks = MemoryHooks()
        cb = CircuitBreakerPolicy(float("inf"), 2**31)
        fm = MagicMock()
        fm.spawn = MagicMock()
        launcher = TaskLauncher(config, hooks, cb, fm, lambda _: None)

        proc = subprocess.Popen(["true"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        proc.wait()
        task = Task(uuid="done-test", command="true")
        rt = _RunningProcess(
            uuid="done-test", task=task, proc=proc,
            started_at=1_700_000_000.0, started_at_mono=10_000.0,
            stderr_buf=io.BytesIO(b""), retry_depth=0,
        )
        launcher._running["done-test"] = rt

        launcher.check_completions()

        assert len(hooks.successes) == 1
        assert not launcher.is_running("done-test")


def _make_launcher(tmp_path: Path):
    from ghdag.dag.circuit_breaker import CircuitBreakerPolicy
    from ghdag.dag.task_launcher import TaskLauncher

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


def test_launch_injects_resume_after_admission(tmp_path: Path) -> None:
    """Issue #6081: resume injection runs only after quota admission allows launch."""
    launcher = _make_launcher(tmp_path)
    launcher._session_store.record("parent-uuid", "claude", "sess-parent")
    original_command = "claude -p 'continue'"
    task = Task(
        uuid="child-uuid",
        command=original_command,
        engine="claude",
        annotations={"resume_from_uuid": "parent-uuid"},
    )
    call_order: list[str] = []
    real_begin = launcher._quota_gate.begin_run

    real_lookup = launcher._session_store.lookup

    def track_begin(**kwargs):
        call_order.append("begin_run")
        return real_begin(**kwargs)

    def track_lookup(key):
        call_order.append("lookup")
        return real_lookup(key)

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

    dummy_proc = MagicMock()
    dummy_proc.stdout = None
    dummy_proc.stderr = io.BytesIO(b"")
    dummy_proc.poll.return_value = None
    dummy_proc.pid = 4242

    with patch.object(launcher._quota_gate, "begin_run", side_effect=track_begin), patch.object(
        launcher._session_store,
        "lookup",
        side_effect=track_lookup,
    ) as mock_lookup, patch(
        "ghdag.dag.task_launcher.compact_resume_session",
        return_value=skipped,
    ), patch(
        "ghdag.dag.task_launcher.write_compaction_audit",
    ), patch(
        "ghdag.dag.task_launcher.subprocess.Popen",
        return_value=dummy_proc,
    ) as mock_popen:
        assert launcher.launch("child-uuid", task) is True

    assert call_order == ["begin_run", "lookup"]
    mock_lookup.assert_called_once_with("parent-uuid")
    popen_argv = mock_popen.call_args[0][0]
    assert "--resume 'sess-parent'" in popen_argv[4]
    assert task.annotations.get("resumed_session_id") == "sess-parent"


def test_quarantined_launch_skips_resume_injection(tmp_path: Path) -> None:
    """Issue #6081: quarantined engines never trigger resume lookup or compaction audit."""
    launcher = _make_launcher(tmp_path)
    launcher._engine_quarantine.enter("claude", cooldown=300)
    launcher._session_store.record("parent-uuid", "claude", "sess-parent")
    task = Task(
        uuid="child-uuid",
        command="claude -p 'continue'",
        engine="claude",
        annotations={"resume_from_uuid": "parent-uuid"},
    )

    with patch.object(
        launcher._session_store,
        "lookup",
        wraps=launcher._session_store.lookup,
    ) as mock_lookup, patch(
        "ghdag.dag.task_launcher.write_compaction_audit",
    ) as mock_audit:
        assert launcher.launch("child-uuid", task) is False

    mock_lookup.assert_not_called()
    mock_audit.assert_not_called()


def test_missing_input_launch_skips_resume_injection(tmp_path: Path) -> None:
    """Issue #6081: missing stdin redirect skips resume before SessionStore.lookup."""
    launcher = _make_launcher(tmp_path)
    launcher._session_store.record("parent-uuid", "claude", "sess-parent")
    task = Task(
        uuid="child-uuid",
        command="claude -p < missing-order.md",
        engine="claude",
        annotations={"resume_from_uuid": "parent-uuid"},
    )

    with patch.object(
        launcher._session_store,
        "lookup",
        wraps=launcher._session_store.lookup,
    ) as mock_lookup, patch(
        "ghdag.dag.task_launcher.write_compaction_audit",
    ) as mock_audit, patch(
        "ghdag.dag.task_launcher.state_mark_done",
    ) as mock_mark_done:
        assert launcher.launch("child-uuid", task) is False

    mock_lookup.assert_not_called()
    mock_audit.assert_not_called()
    mock_mark_done.assert_called_once()
    assert mock_mark_done.call_args[0][2] == DONE_SKIPPED_MISSING_INPUT


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
        "ghdag.dag.task_launcher.subprocess.Popen",
        side_effect=OSError("boom"),
    ):
        with pytest.raises(OSError):
            launcher.launch("child-uuid", task)

    assert task.command == original_command
    assert "_command_without_resume" not in task.annotations
    assert "resumed_session_id" not in task.annotations
    assert "child-uuid" not in launcher._quota_gate.snapshot().running_tasks


def test_apply_resume_codex_search_command(tmp_path: Path) -> None:
    """Issue #6084: codex --search commands get exec resume injected in the right place."""
    launcher = _make_launcher(tmp_path)
    launcher._session_store.record("parent-uuid", "codex", "sess-parent")
    original_command = render_exec_command(
        ENGINE_SPECS["codex"],
        order_path="child.md",
        model="M",
        permission=WEB_RESEARCH,
    )
    task = Task(
        uuid="child-uuid",
        command=original_command,
        engine="codex",
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
    expected_command = (
        "codex --search exec resume 'sess-parent' --model 'M' --json "
        "--skip-git-repo-check < child.md"
    )

    with patch(
        "ghdag.dag.task_launcher.compact_resume_session",
        return_value=skipped,
    ), patch("ghdag.dag.task_launcher.write_compaction_audit"):
        launcher._apply_resume_if_available("child-uuid", task)

    assert task.command == expected_command
    assert task.annotations["_command_without_resume"] == original_command
    assert task.annotations["resumed_session_id"] == "sess-parent"


def _log_message_count(records: list[logging.LogRecord], needle: str) -> int:
    return sum(1 for record in records if needle in record.message)


def test_quota_deferred_launch_does_not_log_launching(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Issue #6502 AC1: quota denial returns False with one deferred line and no Launching."""
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

    assert _log_message_count(caplog.records, "Launching [") == 0
    assert (
        _log_message_count(caplog.records, "launch deferred by quota gate") == 1
    )


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


def test_quota_deferred_log_is_emitted_again_when_reason_changes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Issue #6502 AC3: changing deferral reason emits a new deferred line."""
    launcher = _make_launcher(tmp_path)
    task = Task(uuid="quota-uuid", command="claude -p ok", engine="claude")
    denied_r1 = AdmissionDecision(
        allowed=False,
        status="PAUSED",
        reason="budget: R1",
        resume_at=None,
    )
    denied_r2 = AdmissionDecision(
        allowed=False,
        status="PAUSED",
        reason="budget: R2",
        resume_at=None,
    )

    with caplog.at_level(logging.INFO, logger="ghdag.dag.task_launcher"):
        with patch.object(launcher._quota_gate, "begin_run", return_value=denied_r1):
            assert launcher.launch("quota-uuid", task) is False
        with patch.object(launcher._quota_gate, "begin_run", return_value=denied_r2):
            assert launcher.launch("quota-uuid", task) is False

    assert _log_message_count(caplog.records, "launch deferred by quota gate") == 2


def test_successful_launch_resets_quota_deferred_log_state(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Issue #6502 AC4: after launch, the same deferral reason logs deferred again."""
    launcher = _make_launcher(tmp_path)
    task = Task(uuid="quota-uuid", command="true", engine="shell")
    denied = AdmissionDecision(
        allowed=False,
        status="PAUSED",
        reason="budget: weekly cap",
        resume_at=None,
    )
    allowed = AdmissionDecision(
        allowed=True,
        status="ALLOWED",
        reason=None,
        resume_at=None,
    )
    dummy_proc = MagicMock()
    dummy_proc.stdout = None
    dummy_proc.stderr = io.BytesIO(b"")
    dummy_proc.poll.return_value = None
    dummy_proc.pid = 4242

    with caplog.at_level(logging.INFO, logger="ghdag.dag.task_launcher"):
        with patch.object(launcher._quota_gate, "begin_run", return_value=denied):
            assert launcher.launch("quota-uuid", task) is False
        with patch.object(launcher._quota_gate, "begin_run", return_value=allowed), patch(
            "ghdag.dag.task_launcher.subprocess.Popen",
            return_value=dummy_proc,
        ):
            assert launcher.launch("quota-uuid", task) is True
        launcher._running.clear()
        with patch.object(launcher._quota_gate, "begin_run", return_value=denied):
            assert launcher.launch("quota-uuid", task) is False

    assert _log_message_count(caplog.records, "Launching [quota-uuid]") == 1
    assert _log_message_count(caplog.records, "launch deferred by quota gate") == 2


def test_apply_resume_claude_matches_render(tmp_path: Path) -> None:
    """Issue #6084: claude resume injection matches render_exec_command placement."""
    launcher = _make_launcher(tmp_path)
    launcher._session_store.record("parent-uuid", "claude", "sess-parent")
    original_command = render_exec_command(
        ENGINE_SPECS["claude"],
        order_path="child.md",
        model="M",
    )
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
    expected_command = render_exec_command(
        ENGINE_SPECS["claude"],
        order_path="child.md",
        model="M",
        resume_session_id="sess-parent",
    )

    with patch(
        "ghdag.dag.task_launcher.compact_resume_session",
        return_value=skipped,
    ), patch("ghdag.dag.task_launcher.write_compaction_audit"):
        launcher._apply_resume_if_available("child-uuid", task)

    assert task.command == expected_command
    assert task.annotations["_command_without_resume"] == original_command
    assert task.annotations["resumed_session_id"] == "sess-parent"


# --- Failure stderr artifacts ---


def _stderr_run_config(tmp_path: Path, task: dict):
    jobs = tmp_path / "jobs"
    done = jobs / "done"
    done.mkdir(parents=True)
    audit_path = jobs / "audit.jsonl"
    exec_jsonl = tmp_path / "exec.jsonl"
    exec_jsonl.write_text(json.dumps(task) + "\n", encoding="utf-8")
    config = DagConfig(
        exec_jsonl_path=str(exec_jsonl),
        exec_done_dir=str(done),
        poll_interval=0.05,
        launch_stagger=0.0,
        lock_file=str(tmp_path / "lock"),
    )
    hooks = AuditHooks(audit_path=audit_path)
    engine = DagEngine(config, hooks)
    return jobs, audit_path, engine


def _audit_record(audit_path: Path, event_type: str) -> dict:
    for line in audit_path.read_text().splitlines():
        record = json.loads(line)
        if record.get("event_type") == event_type:
            return record
    raise AssertionError(f"no {event_type} in audit")


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


def test_failed_shell_task_persists_stderr_and_audit_path(tmp_path: Path) -> None:
    """A non-zero shell task saves stderr and audit stderr_path."""
    uuid = "stderr-fail-uuid"
    jobs, audit_path, engine = _stderr_run_config(
        tmp_path,
        {
            "uuid": uuid,
            "command": "echo diagnostic >&2; exit 3",
            "depends": [],
            "retry": 0,
            "annotations": {},
        },
    )
    _run_engine_to_completion(engine, done_dir=jobs / "done", task_uuids=(uuid,))

    artifacts = list((jobs / "stderr" / uuid).glob("*.log"))
    assert len(artifacts) == 1
    assert "diagnostic" in artifacts[0].read_text(encoding="utf-8")
    failed = _audit_record(audit_path, "task_failed")
    assert failed["stderr_path"] == artifacts[0].relative_to(jobs).as_posix()


def test_success_does_not_persist_stderr(tmp_path: Path) -> None:
    """Success leaves no stderr artifact and audit stderr_path null."""
    uuid = "stderr-ok-uuid"
    jobs, audit_path, engine = _stderr_run_config(
        tmp_path,
        {
            "uuid": uuid,
            "command": "echo ok",
            "depends": [],
            "retry": 0,
            "annotations": {},
        },
    )
    _run_engine_to_completion(engine, done_dir=jobs / "done", task_uuids=(uuid,))

    assert not (jobs / "stderr").exists()
    complete = _audit_record(audit_path, "task_complete")
    assert complete["stderr_path"] is None


def test_failure_with_empty_stderr_does_not_persist_artifact(tmp_path: Path) -> None:
    """Failure with an empty diagnostic creates no artifact."""
    from ghdag.dag.circuit_breaker import CircuitBreakerPolicy

    jobs = tmp_path / "jobs"
    done = jobs / "done"
    done.mkdir(parents=True)
    config = DagConfig(
        exec_jsonl_path=str(tmp_path / "exec.jsonl"),
        exec_done_dir=str(done),
    )
    hooks = MemoryHooks()
    launcher = TaskLauncher(
        config,
        hooks,
        CircuitBreakerPolicy(float("inf"), 2**31),
        MagicMock(),
        lambda _: None,
    )
    proc = subprocess.Popen(["false"], stderr=subprocess.PIPE)
    proc.wait()
    task = Task(uuid="empty-stderr", command="false")
    rt = _RunningProcess(
        uuid="empty-stderr",
        task=task,
        proc=proc,
        started_at=1.0,
        started_at_mono=1.0,
        stderr_buf=io.BytesIO(b""),
        retry_depth=0,
    )
    launcher._running["empty-stderr"] = rt
    with patch("ghdag.dag.task_launcher._extract_tee_target", return_value=None):
        launcher.check_completions()

    assert not (jobs / "stderr").exists()
    assert hooks.failures[0][4].stderr_path is None


def test_persisted_stderr_is_redacted(tmp_path: Path) -> None:
    """Secrets in stderr are redacted in the artifact."""
    from ghdag.dag.circuit_breaker import CircuitBreakerPolicy

    secret = "xoxb-1-abc"
    jobs = tmp_path / "jobs"
    done = jobs / "done"
    done.mkdir(parents=True)
    config = DagConfig(exec_jsonl_path=str(tmp_path / "x"), exec_done_dir=str(done))
    launcher = TaskLauncher(
        config,
        MagicMock(),
        CircuitBreakerPolicy(float("inf"), 2**31),
        MagicMock(),
        lambda _: None,
    )
    rel = launcher._persist_failure_stderr("redact-uuid", 0, f"leak {secret} tail")
    assert rel is not None
    body = (jobs / rel).read_bytes().decode("utf-8")
    assert secret not in body
    assert "[REDACTED:" in body


def test_persisted_stderr_keeps_one_mib_tail() -> None:
    """Oversized stderr keeps a <=1 MiB tail with an end marker."""
    marker = "TAIL_MARKER_END"
    prefix = "x" * 1_048_576
    payload = _stderr_payload_bytes(prefix + marker)
    assert len(payload) <= 1_048_576
    assert payload.endswith(marker.encode("utf-8"))


def test_rerun_preserves_previous_stderr_artifact(tmp_path: Path) -> None:
    """Two failures at the same retry depth keep separate files."""
    from ghdag.dag.circuit_breaker import CircuitBreakerPolicy

    jobs = tmp_path / "jobs"
    done = jobs / "done"
    done.mkdir(parents=True)
    config = DagConfig(exec_jsonl_path=str(tmp_path / "x"), exec_done_dir=str(done))
    launcher = TaskLauncher(
        config,
        MagicMock(),
        CircuitBreakerPolicy(float("inf"), 2**31),
        MagicMock(),
        lambda _: None,
    )
    first = launcher._persist_failure_stderr("rerun-uuid", 0, "first-run")
    second = launcher._persist_failure_stderr("rerun-uuid", 0, "second-run")
    assert first is not None and second is not None
    assert first != second
    assert (jobs / first).read_text(encoding="utf-8") == "first-run"
    assert (jobs / second).read_text(encoding="utf-8") == "second-run"


def test_empty_result_persists_stderr_artifact(tmp_path: Path) -> None:
    """An empty result with stderr saves an artifact and audit path."""
    uuid = "stderr-empty-result"
    result_path = str(tmp_path / "empty.md")
    jobs, audit_path, engine = _stderr_run_config(
        tmp_path,
        {
            "uuid": uuid,
            "command": "echo empty-stderr-line >&2; true",
            "depends": [],
            "retry": 0,
            "annotations": {},
            "result_path": result_path,
        },
    )
    _run_engine_to_completion(engine, done_dir=jobs / "done", task_uuids=(uuid,))

    artifacts = list((jobs / "stderr" / uuid).glob("*.log"))
    assert len(artifacts) == 1
    assert "empty-stderr-line" in artifacts[0].read_text(encoding="utf-8")
    empty = _audit_record(audit_path, "task_empty_result")
    assert empty["stderr_path"] == artifacts[0].relative_to(jobs).as_posix()
