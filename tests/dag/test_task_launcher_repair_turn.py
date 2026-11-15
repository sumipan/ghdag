"""Repair-turn relaunch for reply-block and fan-out contract violations (#6249)."""

from __future__ import annotations

import io
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from ghdag.core.stall_guard import StallTracker
from ghdag.core.vocabulary import PIPELINE_REPLY_BLOCK_FAILED
from ghdag.dag.models import DagConfig, Task, _RunningProcess
from ghdag.dag.task_launcher import TaskLauncher
from ghdag.quota import QuotaGate


@dataclass
class _Proc:
    returncode: int = 0

    def poll(self):
        return self.returncode

    def wait(self):
        return self.returncode


def _make_launcher(
    tmp_path: Path,
    *,
    max_repair_turns: int = 1,
    quota_gate: QuotaGate | None = None,
) -> TaskLauncher:
    from ghdag.dag.circuit_breaker import CircuitBreakerPolicy

    jobs = tmp_path / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    done = jobs / "done"
    done.mkdir()
    config = DagConfig(
        exec_jsonl_path=jobs / "exec.jsonl",
        exec_done_dir=done,
        max_repair_turns=max_repair_turns,
    )
    hooks = MagicMock()
    hooks.check_rejected.return_value = False
    hooks.check_pipeline_status.return_value = PIPELINE_REPLY_BLOCK_FAILED
    fm = MagicMock()
    gate = quota_gate or QuotaGate(jobs / "quota-gate.json", audit_path=jobs / "audit.jsonl")
    return TaskLauncher(
        config,
        hooks,
        CircuitBreakerPolicy(float("inf"), 2**31),
        fm,
        lambda _: None,
        quota_gate=gate,
    )


def _seed_running(launcher: TaskLauncher, uuid: str, task: Task) -> None:
    proc = _Proc(returncode=0)
    launcher._running[uuid] = _RunningProcess(
        uuid=uuid,
        task=task,
        proc=proc,  # type: ignore[arg-type]
        started_at=1_700_000_000.0,
        started_at_mono=1_700_000_000.0,
        stderr_buf=io.BytesIO(b""),
        retry_depth=0,
        stdout_buf=io.BytesIO(b""),
    )


class TestRepairTurn:
    def test_invalid_reply_block_triggers_one_repair(self, tmp_path):
        launcher = _make_launcher(tmp_path)
        uuid = "task-1"
        result = tmp_path / "jobs" / "result.md"
        result.write_text("```ghdag\n: bad\n```\n", encoding="utf-8")
        order = tmp_path / "jobs" / "order.md"
        order.write_text("run", encoding="utf-8")
        task = Task(
            uuid=uuid,
            command=f'claude -p --model m < {order}',
            engine="claude",
            model="claude-sonnet-4-6",
            result_path=str(result),
        )
        _seed_running(launcher, uuid, task)
        launcher._session_store.record(uuid, "claude", "sess-1")

        popen_calls: list = []
        real_popen = subprocess.Popen

        def fake_popen(*args, **kwargs):
            popen_calls.append((args, kwargs))
            return real_popen(["true"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        with patch("ghdag.dag.task_launcher.subprocess.Popen", new=fake_popen):
            launcher.check_completions()

        assert len(popen_calls) == 1
        assert launcher.is_running(uuid)
        assert (tmp_path / "jobs" / "done" / uuid).exists() is False
        repair_orders = list(tmp_path.glob(f"jobs/*-claude-order-{uuid}-repair1.md"))
        assert repair_orders
        body = repair_orders[0].read_text(encoding="utf-8")
        assert "REPLY_BLOCK_INVALID" in body
        assert "Reply again" in body
        launched_cmd = popen_calls[0][0][0][4]
        assert "--resume" in launched_cmd
        audit = tmp_path / "jobs" / "audit.jsonl"
        assert audit.exists()
        repair_records = [
            json.loads(line)
            for line in audit.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        rec = next(r for r in repair_records if r.get("event_type") == "llm.repair_turn")
        assert rec["event_type"] == "llm.repair_turn"
        assert rec["turn"] == 1
        assert rec["code"] == "REPLY_BLOCK_INVALID"

    def test_second_violation_fails_without_third_launch(self, tmp_path):
        launcher = _make_launcher(tmp_path)
        uuid = "task-2"
        result = tmp_path / "jobs" / "result.md"
        result.write_text("```ghdag\n: bad\n```\n", encoding="utf-8")
        task = Task(
            uuid=uuid,
            command="claude -p < order.md",
            engine="claude",
            result_path=str(result),
            annotations={"repair_turns": "1"},
        )
        _seed_running(launcher, uuid, task)
        launcher._session_store.record(uuid, "claude", "sess-1")
        with patch("ghdag.dag.task_launcher.subprocess.Popen") as popen:
            launcher.check_completions()
            popen.assert_not_called()
        done = tmp_path / "jobs" / "done" / uuid
        assert done.exists()
        assert done.read_text(encoding="utf-8") == f"PIPELINE_FAILED:{PIPELINE_REPLY_BLOCK_FAILED}"

    def test_no_session_skips_repair(self, tmp_path):
        launcher = _make_launcher(tmp_path)
        uuid = "task-3"
        result = tmp_path / "jobs" / "result.md"
        result.write_text("```ghdag\n: bad\n```\n", encoding="utf-8")
        task = Task(
            uuid=uuid,
            command="claude -p < order.md",
            engine="claude",
            result_path=str(result),
        )
        launcher._running[uuid] = _RunningProcess(
            uuid=uuid,
            task=task,
            proc=_Proc(returncode=0),  # type: ignore[arg-type]
            started_at=1_700_000_000.0,
            started_at_mono=1_700_000_000.0,
            stderr_buf=io.BytesIO(b""),
            retry_depth=0,
            stdout_buf=io.BytesIO(b""),
        )
        with patch("ghdag.dag.task_launcher.subprocess.Popen") as popen:
            launcher.check_completions()
            popen.assert_not_called()

    def test_gemini_skips_repair(self, tmp_path):
        launcher = _make_launcher(tmp_path)
        uuid = "task-4"
        result = tmp_path / "jobs" / "result.md"
        result.write_text("```ghdag\n: bad\n```\n", encoding="utf-8")
        task = Task(
            uuid=uuid,
            command="gemini -p < order.md",
            engine="gemini",
            result_path=str(result),
        )
        _seed_running(launcher, uuid, task)
        launcher._session_store.record(uuid, "gemini", "sess")
        with patch("ghdag.dag.task_launcher.subprocess.Popen") as popen:
            launcher.check_completions()
            popen.assert_not_called()

    def test_max_repair_turns_zero_disables_repair(self, tmp_path):
        launcher = _make_launcher(tmp_path, max_repair_turns=0)
        uuid = "task-5"
        result = tmp_path / "jobs" / "result.md"
        result.write_text("```ghdag\n: bad\n```\n", encoding="utf-8")
        task = Task(
            uuid=uuid,
            command="claude -p < order.md",
            engine="claude",
            result_path=str(result),
        )
        _seed_running(launcher, uuid, task)
        launcher._session_store.record(uuid, "claude", "sess")
        with patch("ghdag.dag.task_launcher.subprocess.Popen") as popen:
            launcher.check_completions()
            popen.assert_not_called()

    def test_repair_turn_registers_running_task(self, tmp_path):
        """Refs sumipan/nexus#6802."""
        launcher = _make_launcher(tmp_path)
        uuid = "task-quota-1"
        result = tmp_path / "jobs" / "result.md"
        result.write_text("```ghdag\n: bad\n```\n", encoding="utf-8")
        order = tmp_path / "jobs" / "order.md"
        order.write_text("run", encoding="utf-8")
        task = Task(
            uuid=uuid,
            command=f'claude -p --model m < {order}',
            engine="claude",
            model="claude-sonnet-4-6",
            result_path=str(result),
        )
        _seed_running(launcher, uuid, task)
        launcher._session_store.record(uuid, "claude", "sess-1")

        repair_proc = MagicMock()
        repair_proc.poll.return_value = None
        repair_proc.pid = 4242
        repair_proc.stdout = io.BytesIO()
        repair_proc.stderr = io.BytesIO()
        real_popen = subprocess.Popen

        def fake_popen(*args, **kwargs):
            if args[0][0] == "true":
                return real_popen(["true"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return repair_proc

        with patch("ghdag.dag.task_launcher.subprocess.Popen", new=fake_popen):
            launcher.check_completions()

        snap = launcher.quota_gate.snapshot()
        assert uuid in snap.running_tasks
        assert snap.running_tasks[uuid].engine == "claude"

    def test_repair_turn_counts_toward_engine_limit(self, tmp_path):
        """Refs sumipan/nexus#6802."""
        jobs = tmp_path / "jobs"
        gate = QuotaGate(jobs / "quota-gate.json", limits={"claude": 1})
        launcher = _make_launcher(tmp_path, quota_gate=gate)
        uuid = "task-quota-2"
        result = tmp_path / "jobs" / "result.md"
        result.write_text("```ghdag\n: bad\n```\n", encoding="utf-8")
        order = tmp_path / "jobs" / "order.md"
        order.write_text("run", encoding="utf-8")
        task = Task(
            uuid=uuid,
            command=f'claude -p < {order}',
            engine="claude",
            result_path=str(result),
        )
        _seed_running(launcher, uuid, task)
        launcher._session_store.record(uuid, "claude", "sess-1")

        repair_proc = MagicMock()
        repair_proc.poll.return_value = None
        repair_proc.pid = 4243
        repair_proc.stdout = io.BytesIO()
        repair_proc.stderr = io.BytesIO()
        real_popen = subprocess.Popen

        def fake_popen(*args, **kwargs):
            if args[0][0] == "true":
                return real_popen(["true"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return repair_proc

        other = Task(
            uuid="other-claude",
            command="claude -p < other.md",
            engine="claude",
            result_path=str(tmp_path / "jobs" / "other-result.md"),
        )
        with patch("ghdag.dag.task_launcher.subprocess.Popen", new=fake_popen):
            launcher.check_completions()
            assert launcher.launch("other-claude", other) is False

    def test_repair_turn_skipped_when_engine_paused(self, tmp_path):
        """Refs sumipan/nexus#6802."""
        jobs = tmp_path / "jobs"
        launcher = _make_launcher(tmp_path)
        launcher.quota_gate.report(
            engine="claude",
            status="paused",
            observed_at=datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc),
            resume_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        )
        uuid = "task-quota-3"
        result = tmp_path / "jobs" / "result.md"
        result.write_text("```ghdag\n: bad\n```\n", encoding="utf-8")
        task = Task(
            uuid=uuid,
            command="claude -p < order.md",
            engine="claude",
            result_path=str(result),
        )
        _seed_running(launcher, uuid, task)
        launcher._session_store.record(uuid, "claude", "sess-1")

        with patch("ghdag.dag.task_launcher.subprocess.Popen") as popen:
            launcher.check_completions()
            popen.assert_not_called()

        done = tmp_path / "jobs" / "done" / uuid
        assert done.read_text(encoding="utf-8") == f"PIPELINE_FAILED:{PIPELINE_REPLY_BLOCK_FAILED}"
        assert not list(tmp_path.glob(f"jobs/*-claude-order-{uuid}-repair1.md"))
        assert not result.with_name("result.md.repair1.md").exists()
        audit = jobs / "audit.jsonl"
        if audit.exists():
            assert "llm.repair_turn" not in audit.read_text(encoding="utf-8")

    def test_repair_turn_finish_clears_running_task(self, tmp_path):
        """Refs sumipan/nexus#6802."""
        launcher = _make_launcher(tmp_path)
        uuid = "task-quota-4"
        result = tmp_path / "jobs" / "result.md"
        result.write_text("```ghdag\n: bad\n```\n", encoding="utf-8")
        order = tmp_path / "jobs" / "order.md"
        order.write_text("run", encoding="utf-8")
        task = Task(
            uuid=uuid,
            command=f'claude -p < {order}',
            engine="claude",
            result_path=str(result),
        )
        _seed_running(launcher, uuid, task)
        launcher._session_store.record(uuid, "claude", "sess-1")
        repair_proc = MagicMock()
        repair_proc.returncode = 0
        repair_proc.pid = 4244
        repair_proc.stdout = io.BytesIO()
        repair_proc.stderr = io.BytesIO()

        def poll_repair():
            if repair_proc.poll.call_count <= 1:
                return None
            return 0

        repair_proc.poll.side_effect = poll_repair

        def fake_popen(*args, **kwargs):
            return repair_proc

        with patch("ghdag.dag.task_launcher.subprocess.Popen", new=fake_popen):
            launcher.check_completions()
            assert uuid in launcher.quota_gate.snapshot().running_tasks
            launcher.check_completions()
            assert uuid in launcher.quota_gate.snapshot().running_tasks
            launcher.check_completions()

        assert uuid not in launcher.quota_gate.snapshot().running_tasks

    def test_repair_order_ts_defaults_to_machine_timezone(self, tmp_path, monkeypatch):
        """Refs sumipan/nexus#6872."""
        import os
        import time
        from zoneinfo import ZoneInfo

        from ghdag.io.audit import file_ts as audit_file_ts

        old_tz = os.environ.get("TZ")
        monkeypatch.setenv("TZ", "Pacific/Auckland")
        time.tzset()
        try:
            launcher = _make_launcher(tmp_path)
            uuid = "task-tz-repair"
            result = tmp_path / "jobs" / "result.md"
            result.write_text("```ghdag\n: bad\n```\n", encoding="utf-8")
            order = tmp_path / "jobs" / "order.md"
            order.write_text("run", encoding="utf-8")
            task = Task(
                uuid=uuid,
                command=f'claude -p --model m < {order}',
                engine="claude",
                model="claude-sonnet-4-6",
                result_path=str(result),
            )
            _seed_running(launcher, uuid, task)
            launcher._session_store.record(uuid, "claude", "sess-1")

            fixed = datetime(2024, 7, 4, 16, 0, 0, tzinfo=ZoneInfo("UTC"))
            expected = audit_file_ts(None, now=fixed)

            def _file_ts(tz_name: str | None) -> str:
                return audit_file_ts(tz_name, now=fixed)

            real_popen = subprocess.Popen

            def fake_popen(*args, **kwargs):
                return real_popen(["true"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            with (
                patch("ghdag.dag.task_launcher.file_ts", side_effect=_file_ts),
                patch("ghdag.dag.task_launcher.subprocess.Popen", new=fake_popen),
            ):
                launcher.check_completions()

            repair_orders = list(tmp_path.glob(f"jobs/*-claude-order-{uuid}-repair1.md"))
            assert repair_orders
            assert repair_orders[0].name.startswith(expected)
        finally:
            if old_tz is None:
                monkeypatch.delenv("TZ", raising=False)
            else:
                monkeypatch.setenv("TZ", old_tz)
            time.tzset()

    def test_repair_turn_relaunch_attaches_stall_tracker_for_cursor(
        self, tmp_path, monkeypatch
    ):
        """Refs sumipan/nexus#6874."""
        monkeypatch.delenv("GHDAG_ENGINE_STALL_GUARD", raising=False)
        launcher = _make_launcher(tmp_path)
        uuid = "task-cursor-stall-repair"
        result = tmp_path / "jobs" / "result.md"
        result.write_text("```ghdag\n: bad\n```\n", encoding="utf-8")
        order = tmp_path / "jobs" / "order.md"
        order.write_text("run", encoding="utf-8")
        task = Task(
            uuid=uuid,
            command=f"agent -p --force < {order}",
            engine="cursor",
            result_path=str(result),
        )
        _seed_running(launcher, uuid, task)
        launcher._session_store.record(uuid, "cursor", "sess-cursor")

        repair_proc = MagicMock()
        repair_proc.poll.return_value = None
        repair_proc.pid = 4242
        repair_proc.stdout = io.BytesIO()
        repair_proc.stderr = io.BytesIO()

        with patch("ghdag.dag.task_launcher.subprocess.Popen", return_value=repair_proc):
            launcher.check_completions()

        rt = launcher._running[uuid]
        assert isinstance(rt.stall_tracker, StallTracker)
        assert rt.stall_tracker._root_pid == 4242
