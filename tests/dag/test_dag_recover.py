"""Tests for ghdag dag recover."""

from __future__ import annotations

import os
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ghdag.dag.recover import (
    RecoverError,
    execute_recover,
    plan_recover,
)
from ghdag.io.done import mark_done
from ghdag.pipeline.task_queue import TaskQueue

FIXED_NOW = datetime(2026, 9, 14, 20, 10, 0, tzinfo=timezone.utc)

UUID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
UUID_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
UUID_C = "cccccccc-cccc-cccc-cccc-cccccccccccc"
KEY = "issuesmith:impl:2876"


def _make_state(tmp_path: Path) -> tuple[TaskQueue, Path, Path, Path, Path]:
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    done = jobs / "done"
    done.mkdir()
    exec_jsonl = jobs / "exec.jsonl"
    exec_jsonl.write_text("", encoding="utf-8")
    state_dir = tmp_path / ".pipeline-state"
    state = TaskQueue(state_dir=state_dir, exec_jsonl_path=exec_jsonl)
    return state, state_dir, exec_jsonl, jobs, done


def _plan_kwargs(
    state_dir: Path,
    exec_jsonl: Path,
    jobs: Path,
    done: Path,
    **kwargs,
) -> dict:
    return {
        "state_dir": state_dir,
        "exec_jsonl_path": exec_jsonl,
        "workflow_name": "issuesmith",
        "handler_name": "impl",
        "issue_number": 2876,
        "queue_dir": jobs,
        "done_dir": done,
        **kwargs,
    }


def _write_order(jobs: Path, uuid: str, content: str = "frozen order") -> Path:
    path = jobs / f"20260905120000-claude-order-{uuid}.md"
    path.write_text(content, encoding="utf-8")
    return path


def _record(
    uuid: str,
    step_name: str,
    *,
    depends: list[str] = [],
    command: str | None = None,
) -> dict:
    order_file = f"jobs/20260905120000-claude-order-{uuid}.md"
    return {
        "uuid": uuid,
        "command": command or f"claude -p {order_file}",
        "depends": depends,
        "result_path": f"jobs/20260905120000-claude-result-{uuid}.md",
        "idempotency_key": KEY,
        "annotations": {"step_name": step_name},
    }


class TestPlanRecover:


    def test_from_step_limits_downstream(self, tmp_path):
        state, state_dir, exec_jsonl, jobs, done = _make_state(tmp_path)
        for uid in (UUID_A, UUID_B, UUID_C):
            _write_order(jobs, uid)
        state.enqueue_records([
            _record(UUID_A, "p1"),
            _record(UUID_B, "cp2-conditional", depends=[UUID_A]),
            _record(UUID_C, "m1-merge", depends=[UUID_B]),
        ])
        mark_done(done, UUID_A, "0")
        mark_done(done, UUID_B, "1")
        mark_done(done, UUID_C, "1")

        plan = plan_recover(
            **_plan_kwargs(state_dir, exec_jsonl, jobs, done, from_step="cp2-conditional"),
        )
        assert UUID_A not in plan.rerun_uuids
        assert UUID_B in plan.rerun_uuids
        assert UUID_C in plan.rerun_uuids


class TestExecuteRecover:

    def test_dry_run_does_not_modify_done_markers(self, tmp_path):
        state, state_dir, exec_jsonl, jobs, done = _make_state(tmp_path)
        _write_order(jobs, UUID_A)
        state.enqueue_records([_record(UUID_A, "p1")])
        mark_done(done, UUID_A, "1")

        plan = plan_recover(**_plan_kwargs(state_dir, exec_jsonl, jobs, done))
        result = execute_recover(plan, queue_dir=jobs, done_dir=done, dry_run=True)
        assert result.recovered == 0
        assert (done / UUID_A).exists()


    def test_missing_order_file_raises_with_redispatch_guidance(self, tmp_path):
        state, state_dir, exec_jsonl, jobs, done = _make_state(tmp_path)
        state.enqueue_records([_record(UUID_A, "p1")])

        plan = plan_recover(**_plan_kwargs(state_dir, exec_jsonl, jobs, done))
        with pytest.raises(RecoverError) as exc_info:
            execute_recover(plan, queue_dir=jobs, done_dir=done)
        err = exc_info.value
        from ghdag.core.exceptions import GhdagError

        assert isinstance(err, GhdagError)
        assert err.code == "RECOVER_NOT_POSSIBLE"
        assert UUID_A in str(err)
        assert "--redispatch" in str(err)


    def test_running_uuids_protects_done_markers(self, tmp_path):
        """Running uuids are excluded from rerun; done markers are not removed."""
        state, state_dir, exec_jsonl, jobs, done = _make_state(tmp_path)
        _write_order(jobs, UUID_A)
        state.enqueue_records([_record(UUID_A, "p1")])
        mark_done(done, UUID_A, "1")

        plan = plan_recover(
            **_plan_kwargs(state_dir, exec_jsonl, jobs, done),
            running_uuids={UUID_A},
        )
        assert UUID_A not in plan.rerun_uuids
        assert next(s for s in plan.steps if s.uuid == UUID_A).status == "running"

        result = execute_recover(
            plan, queue_dir=jobs, done_dir=done, running_uuids={UUID_A},
        )
        assert result.recovered == 0
        assert (done / UUID_A).exists()

    def test_collect_running_uuids_from_jobs_running_dir(self, tmp_path):
        """Stems of jobs/running/*.json can be collected as running_uuids."""
        from ghdag.dag.recover import running_uuids_from_queue_dir

        jobs = tmp_path / "jobs"
        running = jobs / "running"
        running.mkdir(parents=True)
        (running / f"{UUID_A}.json").write_text("{}", encoding="utf-8")
        (running / f"{UUID_B}.json").write_text("{}", encoding="utf-8")
        (running / "not-a-json.txt").write_text("x", encoding="utf-8")

        assert running_uuids_from_queue_dir(jobs) == {UUID_A, UUID_B}
        assert running_uuids_from_queue_dir(tmp_path / "missing") == set()


class TestExecuteRecoverResultArchiving:
    """AC-1: result file archiving on recover."""

    def _result_path(self, jobs: Path, uuid: str) -> Path:
        return jobs / f"20260905120000-claude-result-{uuid}.md"

    def _archived_path(self, jobs: Path, uuid: str) -> Path:
        return jobs / f"20260905120000-claude-result-{uuid}.md.prev-20260914T201000Z"


    def test_keep_results_preserves_result(self, tmp_path):
        state, state_dir, exec_jsonl, jobs, done = _make_state(tmp_path)
        _write_order(jobs, UUID_A)
        result = self._result_path(jobs, UUID_A)
        result.write_text("old", encoding="utf-8")
        state.enqueue_records([_record(UUID_A, "p1")])
        mark_done(done, UUID_A, "1")

        plan = plan_recover(**_plan_kwargs(state_dir, exec_jsonl, jobs, done))
        rv = execute_recover(
            plan, queue_dir=jobs, done_dir=done, keep_results=True, now=FIXED_NOW
        )

        assert result.read_text(encoding="utf-8") == "old"
        assert rv.archived == []
        assert not (done / UUID_A).exists()


    def test_archive_oserror_raises_recover_error_done_preserved(self, tmp_path):
        state, state_dir, exec_jsonl, jobs, done = _make_state(tmp_path)
        _write_order(jobs, UUID_A)
        result = self._result_path(jobs, UUID_A)
        result.write_text("old", encoding="utf-8")
        state.enqueue_records([_record(UUID_A, "p1")])
        mark_done(done, UUID_A, "1")

        plan = plan_recover(**_plan_kwargs(state_dir, exec_jsonl, jobs, done))

        os.chmod(jobs, stat.S_IREAD | stat.S_IEXEC)
        try:
            with pytest.raises(RecoverError, match="failed to archive"):
                execute_recover(plan, queue_dir=jobs, done_dir=done, now=FIXED_NOW)
            assert (done / UUID_A).exists()
        finally:
            os.chmod(jobs, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)


    def test_orphaned_on_restart_step_is_failed_and_rerun(self, tmp_path):
        from ghdag.core.vocabulary import DONE_ORPHANED_ON_RESTART

        state, state_dir, exec_jsonl, jobs, done = _make_state(tmp_path)
        _write_order(jobs, UUID_A)
        state.enqueue_records([_record(UUID_A, "p1")])
        mark_done(done, UUID_A, DONE_ORPHANED_ON_RESTART)

        plan = plan_recover(**_plan_kwargs(state_dir, exec_jsonl, jobs, done))
        step = next(s for s in plan.steps if s.uuid == UUID_A)
        assert step.status == "failed"
        assert UUID_A in plan.rerun_uuids


    def _shell_order_path(self, jobs: Path, uuid: str) -> Path:
        return jobs / f"20260905120000-shell-order-{uuid}.md"

    def _shell_result_path(self, jobs: Path, uuid: str) -> Path:
        return jobs / f"20260905120000-shell-result-{uuid}.md"

    def _shell_archived_path(self, jobs: Path, uuid: str) -> Path:
        return jobs / f"20260905120000-shell-result-{uuid}.md.prev-20260914T201000Z"

    def _shell_record(self, uuid: str, jobs: Path, key: str) -> dict:
        order = self._shell_order_path(jobs, uuid)
        result = self._shell_result_path(jobs, uuid)
        return {
            "uuid": uuid,
            "engine": "shell",
            "command": f"bash -o pipefail {order}",
            "depends": [],
            "result_path": str(result),
            "idempotency_key": key,
            "annotations": {"step_name": "p2"},
        }

    def _make_launcher(self, exec_jsonl: Path, done: Path):
        from unittest.mock import MagicMock

        from ghdag.dag.circuit_breaker import CircuitBreakerPolicy
        from ghdag.dag.hooks import DefaultHooks
        from ghdag.dag.models import DagConfig
        from ghdag.dag.task_launcher import TaskLauncher

        return TaskLauncher(
            DagConfig(
                exec_jsonl_path=exec_jsonl,
                exec_done_dir=done,
                task_timeout=None,
                poll_interval=0.05,
            ),
            hooks=DefaultHooks(),
            circuit_breaker=CircuitBreakerPolicy(float("inf"), 2**31),
            fanout_manager=MagicMock(),
            promote_fn=MagicMock(),
        )


