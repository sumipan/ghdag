"""Tests for ghdag dag recover."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ghdag.dag.recover import (
    RecoverError,
    execute_recover,
    format_recover_plan,
    plan_recover,
)
from ghdag.io.done import mark_done
from ghdag.pipeline.state import PipelineState, build_idempotency_key


UUID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
UUID_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
UUID_C = "cccccccc-cccc-cccc-cccc-cccccccccccc"
KEY = "issuesmith:impl:2876"


def _make_state(tmp_path: Path) -> tuple[PipelineState, Path, Path]:
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    done = jobs / "done"
    done.mkdir()
    exec_jsonl = jobs / "exec.jsonl"
    exec_jsonl.write_text("", encoding="utf-8")
    state = PipelineState(state_dir=tmp_path / ".pipeline-state", exec_jsonl_path=exec_jsonl)
    return state, jobs, done


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
    def test_identifies_failed_and_pending_steps(self, tmp_path):
        state, jobs, done = _make_state(tmp_path)
        _write_order(jobs, UUID_A)
        _write_order(jobs, UUID_B)
        state.append_exec_records([
            _record(UUID_A, "p1"),
            _record(UUID_B, "p2", depends=[UUID_A]),
        ])
        mark_done(done, UUID_A, "0")
        mark_done(done, UUID_B, "1")

        plan = plan_recover(
            state,
            workflow_name="issuesmith",
            handler_name="impl",
            issue_number=2876,
            queue_dir=jobs,
            done_dir=done,
        )
        assert plan.idempotency_key == KEY
        assert plan.rerun_uuids == [UUID_B]

    def test_skips_successful_steps(self, tmp_path):
        state, jobs, done = _make_state(tmp_path)
        _write_order(jobs, UUID_A)
        state.append_exec_records([_record(UUID_A, "p1")])
        mark_done(done, UUID_A, "0")

        plan = plan_recover(
            state,
            workflow_name="issuesmith",
            handler_name="impl",
            issue_number=2876,
            queue_dir=jobs,
            done_dir=done,
        )
        assert plan.rerun_uuids == []

    def test_from_step_limits_downstream(self, tmp_path):
        state, jobs, done = _make_state(tmp_path)
        for uid in (UUID_A, UUID_B, UUID_C):
            _write_order(jobs, uid)
        state.append_exec_records([
            _record(UUID_A, "p1"),
            _record(UUID_B, "cp2-conditional", depends=[UUID_A]),
            _record(UUID_C, "m1-merge", depends=[UUID_B]),
        ])
        mark_done(done, UUID_A, "0")
        mark_done(done, UUID_B, "1")
        mark_done(done, UUID_C, "1")

        plan = plan_recover(
            state,
            workflow_name="issuesmith",
            handler_name="impl",
            issue_number=2876,
            queue_dir=jobs,
            done_dir=done,
            from_step="cp2-conditional",
        )
        assert UUID_A not in plan.rerun_uuids
        assert UUID_B in plan.rerun_uuids
        assert UUID_C in plan.rerun_uuids


class TestExecuteRecover:
    def test_clears_done_markers_for_failed_steps(self, tmp_path):
        state, jobs, done = _make_state(tmp_path)
        _write_order(jobs, UUID_A)
        state.append_exec_records([_record(UUID_A, "p1")])
        mark_done(done, UUID_A, "1")

        plan = plan_recover(
            state,
            workflow_name="issuesmith",
            handler_name="impl",
            issue_number=2876,
            queue_dir=jobs,
            done_dir=done,
        )
        result = execute_recover(plan, queue_dir=jobs, done_dir=done)
        assert result.recovered == 1
        assert not (done / UUID_A).exists()

    def test_dry_run_does_not_modify_done_markers(self, tmp_path):
        state, jobs, done = _make_state(tmp_path)
        _write_order(jobs, UUID_A)
        state.append_exec_records([_record(UUID_A, "p1")])
        mark_done(done, UUID_A, "1")

        plan = plan_recover(
            state,
            workflow_name="issuesmith",
            handler_name="impl",
            issue_number=2876,
            queue_dir=jobs,
            done_dir=done,
        )
        result = execute_recover(plan, queue_dir=jobs, done_dir=done, dry_run=True)
        assert result.recovered == 0
        assert (done / UUID_A).exists()

    def test_dry_run_output_includes_step_details(self, tmp_path):
        state, jobs, done = _make_state(tmp_path)
        _write_order(jobs, UUID_A)
        _write_order(jobs, UUID_B)
        state.append_exec_records([
            _record(UUID_A, "p1"),
            _record(UUID_B, "p2", depends=[UUID_A]),
        ])
        mark_done(done, UUID_A, "0")
        mark_done(done, UUID_B, "1")

        plan = plan_recover(
            state,
            workflow_name="issuesmith",
            handler_name="impl",
            issue_number=2876,
            queue_dir=jobs,
            done_dir=done,
        )
        output = format_recover_plan(plan)
        assert "cp2-conditional" not in output
        assert "p2" in output
        assert "[rerun]" in output
        assert UUID_B in output

    def test_missing_order_file_raises_with_redispatch_guidance(self, tmp_path):
        state, jobs, done = _make_state(tmp_path)
        state.append_exec_records([_record(UUID_A, "p1")])

        plan = plan_recover(
            state,
            workflow_name="issuesmith",
            handler_name="impl",
            issue_number=2876,
            queue_dir=jobs,
            done_dir=done,
        )
        with pytest.raises(RecoverError) as exc_info:
            execute_recover(plan, queue_dir=jobs, done_dir=done)
        assert UUID_A in str(exc_info.value)
        assert "--redispatch" in str(exc_info.value)

    def test_reuses_existing_frozen_order_without_context_hook(self, tmp_path):
        """Recover does not create new order files — only clears done markers."""
        state, jobs, done = _make_state(tmp_path)
        order_path = _write_order(jobs, UUID_A, "frozen content v1")
        state.append_exec_records([_record(UUID_A, "p1")])
        mark_done(done, UUID_A, "1")

        plan = plan_recover(
            state,
            workflow_name="issuesmith",
            handler_name="impl",
            issue_number=2876,
            queue_dir=jobs,
            done_dir=done,
        )
        execute_recover(plan, queue_dir=jobs, done_dir=done)
        assert order_path.read_text(encoding="utf-8") == "frozen content v1"
        assert len(list(jobs.glob("*-order-*.md"))) == 1

    def test_uses_generation_specific_idempotency_key(self, tmp_path):
        state, jobs, done = _make_state(tmp_path)
        gen_key = build_idempotency_key("issuesmith", "impl", 2876, 1)
        state.increment_generation("issuesmith", "impl", 2876)
        _write_order(jobs, UUID_A)
        rec = _record(UUID_A, "p1")
        rec["idempotency_key"] = gen_key
        state.append_exec_records([rec])
        mark_done(done, UUID_A, "1")

        plan = plan_recover(
            state,
            workflow_name="issuesmith",
            handler_name="impl",
            issue_number=2876,
            queue_dir=jobs,
            done_dir=done,
        )
        assert plan.idempotency_key == gen_key
        assert plan.generation == 1
