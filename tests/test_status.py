"""Tests for ghdag.status public API (nexus #3084)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ghdag.status import (
    IssueStatus,
    RunningTask,
    StepStatus,
    issue_status,
    running_tasks,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _write_exec(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
        encoding="utf-8",
    )


def _minimal_graph(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Build a 3-step DAG: a → b → c under tmp_path."""
    exec_jsonl = tmp_path / "exec.jsonl"
    done_dir = tmp_path / "done"
    state_dir = tmp_path / "state"
    done_dir.mkdir()
    state_dir.mkdir()
    (state_dir / "generations.json").write_text("{}", encoding="utf-8")
    records = [
        {
            "uuid": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "command": "echo a",
            "depends": [],
            "annotations": {"step_name": "a"},
            "result_path": "result-a.md",
            "idempotency_key": "wf:handler:1",
        },
        {
            "uuid": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "command": "echo b",
            "depends": ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"],
            "annotations": {"step_name": "b"},
            "result_path": None,
            "idempotency_key": "wf:handler:1",
        },
        {
            "uuid": "cccccccc-cccc-cccc-cccc-cccccccccccc",
            "command": "echo c",
            "depends": ["bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"],
            "annotations": {"step_name": "c"},
            "result_path": "result-c.md",
            "idempotency_key": "wf:handler:1",
        },
    ]
    _write_exec(exec_jsonl, records)
    return exec_jsonl, done_dir, state_dir


class TestIssueStatusUnit:
    def test_issue_status_all_success(self, tmp_path: Path) -> None:
        exec_jsonl, done_dir, state_dir = _minimal_graph(tmp_path)
        for uid in (
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "cccccccc-cccc-cccc-cccc-cccccccccccc",
        ):
            (done_dir / uid).write_text("0", encoding="utf-8")

        result = issue_status(
            1,
            handler="handler",
            workflow="wf",
            exec_jsonl_path=exec_jsonl,
            state_dir=state_dir,
            done_dir=done_dir,
        )
        assert isinstance(result, IssueStatus)
        assert result.generation == 0
        assert result.running is False
        assert result.orphan_uuids == []
        by_name = {s.step_name: s for s in result.steps}
        assert set(by_name) == {"a", "b", "c"}
        for step in result.steps:
            assert isinstance(step, StepStatus)
            assert step.status == "success"
        assert by_name["a"].result_path == "result-a.md"
        assert by_name["b"].result_path is None
        assert by_name["a"].depends == []
        assert by_name["b"].depends == ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"]

    def test_issue_status_running(self, tmp_path: Path) -> None:
        exec_jsonl, done_dir, state_dir = _minimal_graph(tmp_path)
        (done_dir / "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa").write_text(
            "0", encoding="utf-8"
        )
        running = {"bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"}

        result = issue_status(
            1,
            handler="handler",
            workflow="wf",
            exec_jsonl_path=exec_jsonl,
            state_dir=state_dir,
            done_dir=done_dir,
            running_uuids=running,
        )
        assert result.running is True
        by_name = {s.step_name: s for s in result.steps}
        assert by_name["a"].status == "success"
        assert by_name["b"].status == "running"
        assert by_name["c"].status == "pending"

    def test_issue_status_dep_failed(self, tmp_path: Path) -> None:
        exec_jsonl, done_dir, state_dir = _minimal_graph(tmp_path)
        (done_dir / "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa").write_text(
            "1", encoding="utf-8"
        )

        result = issue_status(
            1,
            handler="handler",
            workflow="wf",
            exec_jsonl_path=exec_jsonl,
            state_dir=state_dir,
            done_dir=done_dir,
        )
        by_name = {s.step_name: s for s in result.steps}
        assert by_name["a"].status == "failed"
        assert by_name["b"].status == "dep_failed"
        assert by_name["c"].status == "dep_failed"
        assert result.running is False

    def test_issue_status_orphan(self, tmp_path: Path) -> None:
        exec_jsonl, done_dir, state_dir = _minimal_graph(tmp_path)
        (done_dir / "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa").write_text(
            "0", encoding="utf-8"
        )
        # b and c have no done / not running; b's deps are satisfied → orphan

        result = issue_status(
            1,
            handler="handler",
            workflow="wf",
            exec_jsonl_path=exec_jsonl,
            state_dir=state_dir,
            done_dir=done_dir,
        )
        assert "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb" in result.orphan_uuids
        # c depends on b which has not succeeded → not orphan
        assert "cccccccc-cccc-cccc-cccc-cccccccccccc" not in result.orphan_uuids
        by_name = {s.step_name: s for s in result.steps}
        assert by_name["b"].status == "pending"
        assert by_name["c"].status == "pending"

    def test_status_cancelled(self, tmp_path: Path) -> None:
        """CANCELLED done marker → cancelled (not running, not success)."""
        exec_jsonl, done_dir, state_dir = _minimal_graph(tmp_path)
        (done_dir / "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa").write_text(
            "0", encoding="utf-8"
        )
        (done_dir / "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb").write_text(
            "CANCELLED", encoding="utf-8"
        )

        result = issue_status(
            1,
            handler="handler",
            workflow="wf",
            exec_jsonl_path=exec_jsonl,
            state_dir=state_dir,
            done_dir=done_dir,
            running_uuids=set(),
        )
        by_name = {s.step_name: s for s in result.steps}
        assert by_name["b"].status == "cancelled"
        assert by_name["c"].status == "dep_failed"
        assert result.orphan_uuids == []

    def test_running_tasks(self, tmp_path: Path) -> None:
        running_dir = tmp_path / "running"
        running_dir.mkdir()
        started = "2026-09-10T14:39:20+00:00"
        payload = {
            "pid": 6526,
            "pgid": 6526,
            "engine": "shell",
            "started_at": started,
            "has_resume": False,
        }
        uid = "dddddddd-dddd-dddd-dddd-dddddddddddd"
        (running_dir / f"{uid}.json").write_text(
            json.dumps(payload) + "\n", encoding="utf-8"
        )

        tasks = running_tasks(running_dir)
        assert len(tasks) == 1
        task = tasks[0]
        assert isinstance(task, RunningTask)
        assert task.uuid == uid
        assert task.started_at == started
        assert task.engine == "shell"
        assert task.pid == 6526
        assert task.elapsed_sec >= 0.0
        # started_at is in the past relative to "now"
        started_dt = datetime.fromisoformat(started)
        assert started_dt.tzinfo is not None
        assert task.elapsed_sec > (
            datetime.now(timezone.utc) - started_dt
        ).total_seconds() - 5


class TestIssueStatusFixtures:
    def test_fixture_3059_cancelled_and_dep_failed(self) -> None:
        root = FIXTURES / "issue_3059"
        result = issue_status(
            3059,
            handler="impl",
            workflow="issuesmith",
            exec_jsonl_path=root / "exec.jsonl",
            state_dir=root / "state",
            done_dir=root / "done",
            running_uuids=set(),
        )
        by_name = {s.step_name: s for s in result.steps}
        assert by_name["p0"].status == "success"
        assert by_name["p1"].status == "cancelled"
        for name in ("p2", "p2r", "p3", "cp2", "m1", "m1r", "m2"):
            assert by_name[name].status == "dep_failed", name
        assert result.running is False
        assert result.orphan_uuids == []
        assert result.generation == 0

    def test_fixture_3039_running(self) -> None:
        root = FIXTURES / "issue_3039"
        running_dir = root / "running"
        running = {p.stem for p in running_dir.glob("*.json")}
        assert running  # fixture must include a running marker

        result = issue_status(
            3039,
            handler="impl",
            workflow="issuesmith",
            exec_jsonl_path=root / "exec.jsonl",
            state_dir=root / "state",
            done_dir=root / "done",
            running_uuids=running,
        )
        by_name = {s.step_name: s for s in result.steps}
        assert by_name["p0"].status == "success"
        assert by_name["p1"].status == "success"
        assert by_name["p2"].status == "running"
        assert result.running is True
        # downstream of running p2 are not orphans (deps not all succeeded)
        assert by_name["p2"].uuid not in result.orphan_uuids

        tasks = running_tasks(running_dir)
        assert len(tasks) == 1
        assert tasks[0].uuid == by_name["p2"].uuid
        assert tasks[0].engine == "shell"
        assert tasks[0].started_at == "2026-09-10T14:39:20+00:00"


class TestPublicExports:
    def test_ghdag_package_exports(self) -> None:
        import ghdag

        for name in (
            "IssueStatus",
            "StepStatus",
            "RunningTask",
            "issue_status",
            "running_tasks",
        ):
            assert name in ghdag.__all__
            assert hasattr(ghdag, name)


class TestStepStatusCoreSharedWithPipeline:
    def test_pipeline_task_status_uses_shared_core(self, tmp_path: Path) -> None:
        from ghdag.pipeline.status import STATE_OK, STATE_RUNNING, task_status

        done = tmp_path / "done"
        done.mkdir()
        (done / "u1").write_text("0", encoding="utf-8")
        assert task_status("u1", done) == STATE_OK
        assert task_status("u2", done, running_uuids={"u2"}) == STATE_RUNNING
