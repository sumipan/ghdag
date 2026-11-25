"""Tests for ghdag.maintenance module."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from ghdag.maintenance import repair_exec_jsonl, repair_jobs_done, validate_exec_jsonl

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write_jsonl(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# AC1: validate_exec_jsonl
# ---------------------------------------------------------------------------


class TestValidateExecJsonl:
    def test_all_valid_returns_empty(self, tmp_path):
        f = tmp_path / "exec.jsonl"
        write_jsonl(f, ['{"uuid":"a"}', '{"uuid":"b"}'])
        assert validate_exec_jsonl(f) == []

    def test_second_line_invalid(self, tmp_path):
        f = tmp_path / "exec.jsonl"
        write_jsonl(f, ['{"uuid":"a"}', "not json"])
        result = validate_exec_jsonl(f)
        assert result == [(2, "not json")]

    def test_empty_lines_skipped(self, tmp_path):
        f = tmp_path / "exec.jsonl"
        f.write_text('{"uuid":"a"}\n\n   \n{"uuid":"b"}\n', encoding="utf-8")
        assert validate_exec_jsonl(f) == []

    def test_file_not_found_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            validate_exec_jsonl(tmp_path / "nonexistent.jsonl")


# ---------------------------------------------------------------------------
# AC2: repair_exec_jsonl
# ---------------------------------------------------------------------------


class TestRepairExecJsonl:

    def test_dry_run_does_not_modify_file(self, tmp_path):
        f = tmp_path / "exec.jsonl"
        original = '{"uuid":"a"}\nnot json\n{"uuid":"b"}\n'
        f.write_text(original, encoding="utf-8")
        removed = repair_exec_jsonl(f, dry_run=True)
        assert removed == 1
        assert f.read_text(encoding="utf-8") == original

    def test_empty_and_whitespace_lines_stripped_from_exec_jsonl(self, tmp_path):
        f = tmp_path / "exec.jsonl"
        f.write_text('{"uuid":"a"}\n\n   \n{"uuid":"b"}\n', encoding="utf-8")
        removed = repair_exec_jsonl(f)
        assert removed == 2
        lines = [ln for ln in f.read_text(encoding="utf-8").splitlines() if ln]
        assert lines == ['{"uuid":"a"}', '{"uuid":"b"}']


# ---------------------------------------------------------------------------
# AC3: repair_jobs_done
# ---------------------------------------------------------------------------


class TestRepairJobsDone:
    def _make_exec_jsonl(self, path: Path, entries: list[dict]) -> None:
        lines = [json.dumps(e) for e in entries]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _make_result_file(self, jobs_dir: Path, name: str, content: str) -> Path:
        p = jobs_dir / name
        p.write_text(content, encoding="utf-8")
        return p

    def test_reply_block_rejected_writes_rejected_status(self, tmp_path):
        """Issue #7919 AC2 — repair_jobs_done uses default_check_rejected."""
        jobs_dir = tmp_path / "jobs"
        jobs_dir.mkdir()
        done_dir = jobs_dir / "done"
        done_dir.mkdir()
        self._make_result_file(
            jobs_dir,
            "result-a.md",
            "```ghdag\nstatus: rejected\nreason: too long\n```\n",
        )
        exec_jsonl = jobs_dir / "exec.jsonl"
        self._make_exec_jsonl(exec_jsonl, [
            {"uuid": "aaa", "result_path": "result-a.md"},
        ])
        stats = repair_jobs_done(exec_jsonl, done_dir)
        assert stats == {"restored": 1, "skipped": 0}
        assert (done_dir / "aaa").read_text() == "REJECTED"

    def test_legacy_rejected_line_writes_success_status(self, tmp_path):
        """Issue #7919 AC2 — first-line REJECTED: only is not treated as rejected."""
        jobs_dir = tmp_path / "jobs"
        jobs_dir.mkdir()
        done_dir = jobs_dir / "done"
        done_dir.mkdir()
        self._make_result_file(jobs_dir, "result-a.md", "REJECTED: too long")
        exec_jsonl = jobs_dir / "exec.jsonl"
        self._make_exec_jsonl(exec_jsonl, [
            {"uuid": "aaa", "result_path": "result-a.md"},
        ])
        stats = repair_jobs_done(exec_jsonl, done_dir)
        assert stats == {"restored": 1, "skipped": 0}
        assert (done_dir / "aaa").read_text() == "0"

    def test_empty_result_writes_empty_result_status(self, tmp_path):
        jobs_dir = tmp_path / "jobs"
        jobs_dir.mkdir()
        done_dir = jobs_dir / "done"
        done_dir.mkdir()
        self._make_result_file(jobs_dir, "result-a.md", "")
        exec_jsonl = jobs_dir / "exec.jsonl"
        self._make_exec_jsonl(exec_jsonl, [
            {"uuid": "aaa", "result_path": "result-a.md"},
        ])
        stats = repair_jobs_done(exec_jsonl, done_dir)
        assert stats == {"restored": 1, "skipped": 0}
        assert (done_dir / "aaa").read_text() == "EMPTY_RESULT"

    def test_existing_marker_skipped(self, tmp_path):
        jobs_dir = tmp_path / "jobs"
        jobs_dir.mkdir()
        done_dir = jobs_dir / "done"
        done_dir.mkdir()
        self._make_result_file(jobs_dir, "result-a.md", "output")
        (done_dir / "aaa").write_text("0")
        exec_jsonl = jobs_dir / "exec.jsonl"
        self._make_exec_jsonl(exec_jsonl, [
            {"uuid": "aaa", "result_path": "result-a.md"},
        ])
        stats = repair_jobs_done(exec_jsonl, done_dir)
        assert stats == {"restored": 0, "skipped": 1}

    def test_dry_run_does_not_create_marker(self, tmp_path):
        jobs_dir = tmp_path / "jobs"
        jobs_dir.mkdir()
        done_dir = jobs_dir / "done"
        done_dir.mkdir()
        self._make_result_file(jobs_dir, "result-a.md", "output")
        exec_jsonl = jobs_dir / "exec.jsonl"
        self._make_exec_jsonl(exec_jsonl, [
            {"uuid": "aaa", "result_path": "result-a.md"},
        ])
        stats = repair_jobs_done(exec_jsonl, done_dir, dry_run=True)
        assert stats == {"restored": 1, "skipped": 0}
        assert not (done_dir / "aaa").exists()

    def test_missing_result_path_not_counted(self, tmp_path):
        jobs_dir = tmp_path / "jobs"
        jobs_dir.mkdir()
        done_dir = jobs_dir / "done"
        done_dir.mkdir()
        exec_jsonl = jobs_dir / "exec.jsonl"
        self._make_exec_jsonl(exec_jsonl, [
            {"uuid": "aaa", "result_path": "nonexistent.md"},
        ])
        stats = repair_jobs_done(exec_jsonl, done_dir)
        assert stats == {"restored": 0, "skipped": 0}


_EXECUTION_PACKAGES = frozenset(
    {"ghdag.dag", "ghdag.pipeline", "ghdag.workflow", "ghdag.cli", "ghdag.ui"}
)


def _module_root(name: str | None) -> str | None:
    if name is None:
        return None
    parts = name.split(".")
    if len(parts) < 2:
        return name
    return ".".join(parts[:2])


def test_maintenance_does_not_import_execution_packages() -> None:
    """Issue #7967 AC2 — maintenance must not import execution packages."""
    source_path = Path(__file__).resolve().parents[1] / "src" / "ghdag" / "maintenance.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = _module_root(alias.name)
                if root in _EXECUTION_PACKAGES:
                    offenders.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            root = _module_root(node.module)
            if root in _EXECUTION_PACKAGES:
                offenders.append(node.module)
    assert offenders == []


# ---------------------------------------------------------------------------
# AC4: public API import
# ---------------------------------------------------------------------------


def test_public_api_importable():
    from ghdag.maintenance import repair_exec_jsonl, repair_jobs_done, validate_exec_jsonl  # noqa: F401
