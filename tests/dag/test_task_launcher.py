"""Tests for ghdag.dag.engine — §5.4 acceptance criteria."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from ghdag_dag_conftest_helpers import (
    MemoryHooks,
    _make_config,
)

from ghdag.core.capabilities import WEB_RESEARCH
from ghdag.core.command import render_exec_command
from ghdag.core.engine_spec import ENGINE_SPECS
from ghdag.core.vocabulary import DONE_SKIPPED_MISSING_INPUT
from ghdag.dag.models import DagConfig, Task
from ghdag.llm.compaction import CompactionResult


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
