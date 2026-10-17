"""exec.jsonl duplicate uuid guard (Issue #5372)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ghdag.io.audit import AuditContext


def _append(path: Path, audit: Path, uuid: str, command: str = "echo") -> None:
    from ghdag.io.exec_jsonl import append

    append(
        path,
        [{"uuid": uuid, "command": command}],
        AuditContext(source="test"),
        audit_path=audit,
    )


class TestExecJsonlDuplicateGuard:
    def test_second_append_same_incomplete_uuid_raises(self, tmp_path: Path) -> None:
        from ghdag.io.exec_jsonl import DuplicateTaskError

        exec_path = tmp_path / "exec.jsonl"
        audit = tmp_path / "audit.jsonl"
        done_dir = tmp_path / "done"
        done_dir.mkdir()

        _append(exec_path, audit, "u1")
        with pytest.raises(DuplicateTaskError):
            _append(exec_path, audit, "u1")

    def test_reappend_after_done_marker_is_allowed(self, tmp_path: Path) -> None:
        from ghdag.io.done import mark_done
        from ghdag.io.exec_jsonl import parse, read

        exec_path = tmp_path / "exec.jsonl"
        audit = tmp_path / "audit.jsonl"
        done_dir = tmp_path / "done"
        done_dir.mkdir()

        _append(exec_path, audit, "u1", "echo first")
        mark_done(done_dir, "u1", 0)
        _append(exec_path, audit, "u1", "echo retry")

        tasks = parse(read(exec_path))
        by_uuid = {t.uuid: t.command for t in tasks}
        assert by_uuid["u1"] == "echo retry"

    def test_different_uuids_both_allowed(self, tmp_path: Path) -> None:
        from ghdag.io.exec_jsonl import read

        exec_path = tmp_path / "exec.jsonl"
        audit = tmp_path / "audit.jsonl"

        _append(exec_path, audit, "u1")
        _append(exec_path, audit, "u2")
        lines = [json.loads(line) for line in read(exec_path).splitlines() if line.strip()]
        assert {r["uuid"] for r in lines} == {"u1", "u2"}
