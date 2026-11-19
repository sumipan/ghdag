"""Tests for ghdag.status public API (nexus #3084)."""

from __future__ import annotations

import json
from pathlib import Path

from ghdag.status import issue_status, running_tasks

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


    def test_issue_status_pending_step_has_no_done_fields(self, tmp_path: Path) -> None:
        exec_jsonl, done_dir, state_dir = _minimal_graph(tmp_path)

        result = issue_status(
            1,
            handler="handler",
            workflow="wf",
            exec_jsonl_path=exec_jsonl,
            state_dir=state_dir,
            done_dir=done_dir,
        )
        step_b = next(s for s in result.steps if s.step_name == "b")
        assert step_b.status == "pending"
        assert step_b.done_value is None
        assert step_b.failure_class is None
        assert step_b.cause is None
        assert step_b.retry_policy is None

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


class TestIssueStatusExclusiveGate:
    def test_issue_status_pending_step_blocked_by_exclusive(
        self, tmp_path: Path, capsys
    ) -> None:
        """Refs #7363 — pending steps blocked by exclusive gate expose blocked_by."""
        from ghdag.cli import main
        from ghdag.status import EXCLUSIVE_STATE_FILE

        gate_uuid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        step_uuid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        exec_jsonl = tmp_path / "exec.jsonl"
        done_dir = tmp_path / "done"
        state_dir = tmp_path / "state"
        done_dir.mkdir()
        state_dir.mkdir()
        (state_dir / "generations.json").write_text("{}", encoding="utf-8")
        _write_exec(
            exec_jsonl,
            [
                {
                    "uuid": gate_uuid,
                    "command": "sleep 1",
                    "depends": [],
                    "annotations": {"step_name": "x", "_exclusive": "true"},
                    "idempotency_key": "wf:handler:1",
                },
                {
                    "uuid": step_uuid,
                    "command": "echo s",
                    "depends": [],
                    "annotations": {"step_name": "s"},
                    "idempotency_key": "wf:handler:1",
                },
            ],
        )
        (tmp_path / EXCLUSIVE_STATE_FILE).write_text(
            json.dumps({"uuid": gate_uuid, "state": "waiting"}),
            encoding="utf-8",
        )

        result = issue_status(
            1,
            handler="handler",
            workflow="wf",
            exec_jsonl_path=exec_jsonl,
            state_dir=state_dir,
            done_dir=done_dir,
        )
        step_s = next(s for s in result.steps if s.step_name == "s")
        assert step_s.status == "pending"
        assert step_s.blocked_by == gate_uuid

        import os

        os.chdir(tmp_path)
        main(
            [
                "status",
                "--issue",
                "1",
                "--handler",
                "handler",
                "--workflow",
                "wf",
                "--exec-jsonl",
                str(exec_jsonl),
                "--state-dir",
                str(state_dir),
                "--done-dir",
                str(done_dir),
            ]
        )
        text = capsys.readouterr().out
        assert "blocked_by=" + gate_uuid in text

        main(
            [
                "status",
                "--issue",
                "1",
                "--handler",
                "handler",
                "--workflow",
                "wf",
                "--exec-jsonl",
                str(exec_jsonl),
                "--state-dir",
                str(state_dir),
                "--done-dir",
                str(done_dir),
                "--json",
            ]
        )
        payload = json.loads(capsys.readouterr().out)
        json_step = next(s for s in payload["steps"] if s["step_name"] == "s")
        assert json_step["blocked_by"] == gate_uuid


def test_status_running_task_remains_public() -> None:
    from ghdag.status import RunningTask

    assert RunningTask.__name__ == "RunningTask"
    import ghdag

    assert "RunningTask" not in ghdag.__all__
    assert not hasattr(ghdag, "RunningTask")


