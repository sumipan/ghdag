"""Repair-turn relaunch for reply-block and fan-out contract violations (#6249)."""

from __future__ import annotations

import io
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

from ghdag.core.vocabulary import PIPELINE_REPLY_BLOCK_FAILED
from ghdag.dag.models import DagConfig, Task, _RunningProcess
from ghdag.dag.task_launcher import TaskLauncher


@dataclass
class _Proc:
    returncode: int = 0

    def poll(self):
        return self.returncode

    def wait(self):
        return self.returncode


def _make_launcher(tmp_path: Path, *, max_repair_turns: int = 1) -> TaskLauncher:
    from ghdag.dag.circuit_breaker import CircuitBreakerPolicy

    jobs = tmp_path / "jobs"
    jobs.mkdir(parents=True)
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
    return TaskLauncher(
        config,
        hooks,
        CircuitBreakerPolicy(float("inf"), 2**31),
        fm,
        lambda _: None,
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
        rec = json.loads(audit.read_text(encoding="utf-8").strip())
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
