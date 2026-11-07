"""Tests for ghdag.io.exec_jsonl — exec.jsonl I/O consolidation (nexus Issue #2674)."""

from __future__ import annotations

import fcntl
import json
from pathlib import Path

import pytest


class TestRead:
    def test_read_returns_file_contents(self, tmp_path: Path) -> None:
        from ghdag.io import exec_jsonl

        path = tmp_path / "exec.jsonl"
        path.write_text('{"uuid":"a","command":"echo"}\n', encoding="utf-8")
        assert exec_jsonl.read(path) == '{"uuid":"a","command":"echo"}\n'

    def test_read_uses_lock_sh(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from ghdag.io import exec_jsonl

        locks: list[int] = []
        real_flock = fcntl.flock

        def spy(fd, op):
            locks.append(op)
            return real_flock(fd, op)

        monkeypatch.setattr(fcntl, "flock", spy)
        path = tmp_path / "exec.jsonl"
        path.write_text("{}\n", encoding="utf-8")
        exec_jsonl.read(path)
        assert fcntl.LOCK_SH in locks
        assert fcntl.LOCK_UN in locks


class TestParse:
    def test_parse_returns_tasks_last_uuid_wins(self) -> None:
        from ghdag.io.exec_jsonl import parse

        text = "\n".join([
            json.dumps({"uuid": "a", "command": "echo 1"}),
            json.dumps({"uuid": "a", "command": "echo 2"}),
            json.dumps({"uuid": "b", "command": "echo b"}),
        ])
        tasks = parse(text)
        assert [t.uuid for t in tasks] == ["a", "b"]
        assert tasks[0].command == "echo 2"

    def test_parse_jsonl_shim_delegates(self) -> None:
        from ghdag.dag.parser import parse_jsonl
        from ghdag.io.exec_jsonl import parse

        text = json.dumps({"uuid": "x", "command": "true"}) + "\n"
        assert [t.uuid for t in parse_jsonl(text)] == [t.uuid for t in parse(text)]


class TestParseAsDict:
    def test_parse_as_dict(self, tmp_path: Path) -> None:
        from ghdag.io.exec_jsonl import parse_as_dict

        path = tmp_path / "exec.jsonl"
        path.write_text(
            json.dumps({"uuid": "u1", "command": "echo 1"}) + "\n"
            + json.dumps({"uuid": "u2", "command": "echo 2"}) + "\n",
            encoding="utf-8",
        )
        assert parse_as_dict(path) == {"u1": "echo 1", "u2": "echo 2"}


class TestCheckIdempotency:
    def test_missing_key_is_unprocessed(self, tmp_path: Path) -> None:
        from ghdag.io.exec_jsonl import check_idempotency

        path = tmp_path / "exec.jsonl"
        path.write_text(
            json.dumps({"uuid": "u", "command": "x", "idempotency_key": "k1"}) + "\n",
            encoding="utf-8",
        )
        assert check_idempotency(path, "k2") is True
        assert check_idempotency(path, "k1") is False

    def test_missing_file_is_unprocessed(self, tmp_path: Path) -> None:
        from ghdag.io.exec_jsonl import check_idempotency

        assert check_idempotency(tmp_path / "missing.jsonl", "any") is True


class TestAppend:
    def test_append_writes_jsonl_and_audit(self, tmp_path: Path) -> None:
        from ghdag.io.audit import AuditContext
        from ghdag.io.exec_jsonl import append

        path = tmp_path / "exec.jsonl"
        audit = tmp_path / "audit.jsonl"
        records = [{"uuid": "u1", "command": "echo"}]
        append(path, records, AuditContext(source="test", correlation_id="c1"), audit_path=audit)

        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["uuid"] == "u1"

        audit_rec = json.loads(audit.read_text(encoding="utf-8").strip())
        assert audit_rec["source"] == "test"
        assert audit_rec["correlation_id"] == "c1"
        assert audit_rec["task_uuids"] == ["u1"]
        assert audit_rec["exec_lines_count"] == 1

    def test_append_uses_lock_ex(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from ghdag.io import exec_jsonl
        from ghdag.io.audit import AuditContext

        locks: list[int] = []
        real_flock = fcntl.flock

        def spy(fd, op):
            locks.append(op)
            return real_flock(fd, op)

        monkeypatch.setattr(fcntl, "flock", spy)
        path = tmp_path / "exec.jsonl"
        append = exec_jsonl.append
        append(path, [{"uuid": "a", "command": "x"}], AuditContext(), audit_path=tmp_path / "audit.jsonl")
        assert fcntl.LOCK_EX in locks

    def test_append_injects_request_id(self, tmp_path: Path) -> None:
        from ghdag.io.audit import AuditContext
        from ghdag.io.exec_jsonl import append

        path = tmp_path / "exec.jsonl"
        records = [{"uuid": "u1", "command": "echo"}]
        append(
            path,
            records,
            AuditContext(request_id="req-1"),
            audit_path=tmp_path / "audit.jsonl",
        )
        written = json.loads(path.read_text(encoding="utf-8").strip())
        assert written["annotations"]["_request_id"] == "req-1"

    def test_append_does_not_create_task_level_deferred_ledger(self, tmp_path: Path) -> None:
        from datetime import datetime, timedelta, timezone

        from ghdag.io.audit import AuditContext
        from ghdag.io.exec_jsonl import append
        from ghdag.quota import QuotaGate

        jst = timezone(timedelta(hours=9))
        path = tmp_path / "exec.jsonl"
        gate = QuotaGate(tmp_path / "quota-gate.json", pause_ttl_seconds=None)
        gate.report(
            engine="claude",
            status="paused",
            observed_at=datetime(2026, 9, 2, 12, 0, tzinfo=jst),
        )
        records = [{"uuid": "u1", "command": "claude -p hi", "engine": "claude"}]
        append(
            path,
            records,
            AuditContext(source="test"),
            audit_path=tmp_path / "audit.jsonl",
            quota_gate=gate,
        )
        snap = gate.snapshot()
        assert "u1" not in snap.running_tasks
        assert snap.engines["claude"].status == "paused"


class TestRemove:
    def test_remove_by_predicate(self, tmp_path: Path) -> None:
        from ghdag.io.exec_jsonl import remove_by_predicate

        path = tmp_path / "exec.jsonl"
        path.write_text(
            json.dumps({"uuid": "a", "command": "1", "idempotency_key": "keep"}) + "\n"
            + json.dumps({"uuid": "b", "command": "2", "idempotency_key": "drop"}) + "\n",
            encoding="utf-8",
        )
        removed = remove_by_predicate(path, lambda r: r.get("idempotency_key") == "drop")
        assert removed == 1
        remaining = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert [r["uuid"] for r in remaining] == ["a"]

    def test_remove_by_uuids(self, tmp_path: Path) -> None:
        from ghdag.io.exec_jsonl import remove_by_uuids

        path = tmp_path / "exec.jsonl"
        path.write_text(
            json.dumps({"uuid": "a", "command": "1"}) + "\n"
            + json.dumps({"uuid": "b", "command": "2"}) + "\n",
            encoding="utf-8",
        )
        assert remove_by_uuids(path, {"a"}) == 1
        remaining = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert [r["uuid"] for r in remaining] == ["b"]

    def test_remove_uses_lock_ex(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from ghdag.io import exec_jsonl

        locks: list[int] = []
        real_flock = fcntl.flock

        def spy(fd, op):
            locks.append(op)
            return real_flock(fd, op)

        monkeypatch.setattr(fcntl, "flock", spy)
        path = tmp_path / "exec.jsonl"
        path.write_text(json.dumps({"uuid": "a", "command": "x"}) + "\n", encoding="utf-8")
        exec_jsonl.remove_by_uuids(path, {"a"})
        assert fcntl.LOCK_EX in locks


class TestPruneAndLoad:
    def test_load_uuids(self, tmp_path: Path) -> None:
        from ghdag.io.exec_jsonl import load_uuids

        path = tmp_path / "exec.jsonl"
        path.write_text(
            json.dumps({"uuid": "AbC", "command": "x"}) + "\n",
            encoding="utf-8",
        )
        lines, uuids = load_uuids(path)
        assert len(lines) == 1
        assert "abc" in uuids

    def test_prune_removes_and_locks(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from ghdag.io import exec_jsonl

        locks: list[int] = []
        real_flock = fcntl.flock

        def spy(fd, op):
            locks.append(op)
            return real_flock(fd, op)

        monkeypatch.setattr(fcntl, "flock", spy)
        path = tmp_path / "exec.jsonl"
        path.write_text(
            json.dumps({"uuid": "keep", "command": "1"}) + "\n"
            + json.dumps({"uuid": "drop", "command": "2"}) + "\n",
            encoding="utf-8",
        )
        assert exec_jsonl.prune(path, {"drop"}) == 1
        assert fcntl.LOCK_EX in locks
        text = path.read_text(encoding="utf-8")
        assert "keep" in text and "drop" not in text

    def test_prune_dry_run_does_not_write(self, tmp_path: Path) -> None:
        from ghdag.io.exec_jsonl import prune

        path = tmp_path / "exec.jsonl"
        original = json.dumps({"uuid": "drop", "command": "2"}) + "\n"
        path.write_text(original, encoding="utf-8")
        assert prune(path, {"drop"}, dry_run=True) == 1
        assert path.read_text(encoding="utf-8") == original


def test_parse_idempotency_key_roundtrips_build():
    from ghdag.io.exec_jsonl import build_idempotency_key, parse_idempotency_key

    assert parse_idempotency_key("wf:h:3:2") == ("wf", "h", 3, 2)
    assert parse_idempotency_key(build_idempotency_key("wf", "h", 3)) == ("wf", "h", 3, 0)
    assert parse_idempotency_key(build_idempotency_key("wf", "h", 3, 2)) == ("wf", "h", 3, 2)


def test_parse_idempotency_key_rejects_malformed():
    from ghdag.io.exec_jsonl import parse_idempotency_key

    for bad in ("garbage", "wf:h:x", "wf::3", "wf:h:3:2:9"):
        assert parse_idempotency_key(bad) is None


class TestValidateRepair:
    def test_validate(self, tmp_path: Path) -> None:
        from ghdag.io.exec_jsonl import validate

        path = tmp_path / "exec.jsonl"
        path.write_text('{"uuid":"a","command":"x"}\nNOT JSON\n', encoding="utf-8")
        assert validate(path) == [(2, "NOT JSON")]

    def test_repair(self, tmp_path: Path) -> None:
        from ghdag.io.exec_jsonl import repair

        path = tmp_path / "exec.jsonl"
        path.write_text('{"uuid":"a","command":"x"}\nNOT JSON\n\n', encoding="utf-8")
        assert repair(path) == 2
        assert path.read_text(encoding="utf-8").strip() == '{"uuid":"a","command":"x"}'

    def test_repair_uses_lock_ex(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from ghdag.io import exec_jsonl

        locks: list[int] = []
        real_flock = fcntl.flock

        def spy(fd, op):
            locks.append(op)
            return real_flock(fd, op)

        monkeypatch.setattr(fcntl, "flock", spy)
        path = tmp_path / "exec.jsonl"
        path.write_text("bad\n", encoding="utf-8")
        exec_jsonl.repair(path)
        assert fcntl.LOCK_EX in locks
