"""Tests for ghdag.maintenance module."""

from __future__ import annotations

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

    def test_multiple_invalid_lines(self, tmp_path):
        f = tmp_path / "exec.jsonl"
        write_jsonl(f, ['{"uuid":"a"}', "bad1", '{"uuid":"b"}', "bad2"])
        result = validate_exec_jsonl(f)
        assert result == [(2, "bad1"), (4, "bad2")]

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
    def test_removes_invalid_line(self, tmp_path):
        f = tmp_path / "exec.jsonl"
        write_jsonl(f, ['{"uuid":"a"}', "not json", '{"uuid":"b"}'])
        removed = repair_exec_jsonl(f)
        assert removed == 1
        lines = [ln for ln in f.read_text(encoding="utf-8").splitlines() if ln]
        assert lines == ['{"uuid":"a"}', '{"uuid":"b"}']

    def test_dry_run_does_not_modify_file(self, tmp_path):
        f = tmp_path / "exec.jsonl"
        original = '{"uuid":"a"}\nnot json\n{"uuid":"b"}\n'
        f.write_text(original, encoding="utf-8")
        removed = repair_exec_jsonl(f, dry_run=True)
        assert removed == 1
        assert f.read_text(encoding="utf-8") == original

    def test_all_valid_returns_zero(self, tmp_path):
        f = tmp_path / "exec.jsonl"
        write_jsonl(f, ['{"uuid":"a"}', '{"uuid":"b"}'])
        original = f.read_text(encoding="utf-8")
        removed = repair_exec_jsonl(f)
        assert removed == 0
        assert f.read_text(encoding="utf-8") == original

    def test_empty_and_whitespace_lines_removed(self, tmp_path):
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

    def test_restores_missing_done_marker(self, tmp_path):
        jobs_dir = tmp_path / "jobs"
        jobs_dir.mkdir()
        done_dir = jobs_dir / "done"
        done_dir.mkdir()
        self._make_result_file(jobs_dir, "result-a.md", "some output")
        exec_jsonl = jobs_dir / "exec.jsonl"
        self._make_exec_jsonl(exec_jsonl, [
            {"uuid": "aaa", "result_path": "result-a.md"},
        ])
        stats = repair_jobs_done(exec_jsonl, done_dir)
        assert stats == {"restored": 1, "skipped": 0}
        assert (done_dir / "aaa").read_text() == "0"

    def test_rejected_result_writes_rejected_status(self, tmp_path):
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
        assert (done_dir / "aaa").read_text() == "REJECTED"

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


# ---------------------------------------------------------------------------
# AC4: public API import
# ---------------------------------------------------------------------------


def test_public_api_importable():
    from ghdag.maintenance import repair_exec_jsonl, repair_jobs_done, validate_exec_jsonl  # noqa: F401
