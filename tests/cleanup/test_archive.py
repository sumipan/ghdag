"""Tests for ghdag.cleanup — AC1–AC10 and Issue-856 fix coverage."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
from ghdag_cleanup_conftest_helpers import (
    TS,
    UUID_A,
    UUID_B,
    UUID_C,
    _make_done_flag,
    _make_exec_jsonl,
    _make_queue_files,
    _set_mtime,
    _setup_dirs,
)

from ghdag.cleanup.orchestrator import cleanup_queue


class TestArchivedDone:
    def test_done_task_newer_than_cutoff_is_not_archived(self, tmp_path):
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        order, result = _make_queue_files(queue_dir, UUID_A)
        _set_mtime(order, days_ago=0.5)
        _make_done_flag(done_dir, UUID_A)
        _make_exec_jsonl(exec_md, [UUID_A])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_jsonl=exec_md,
            cutoff_days=1,
        )

        assert res.archived_done == 0
        assert order.exists()
        assert result.exists()


class TestArchivedOrphan:
    def test_orphan_task_newer_than_orphan_days_is_not_archived(self, tmp_path):
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        order, result = _make_queue_files(queue_dir, UUID_B)
        _set_mtime(order, days_ago=3)
        _make_exec_jsonl(exec_md, [UUID_B])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_jsonl=exec_md,
            orphan_days=7,
        )

        assert res.archived_orphan == 0
        assert order.exists()


# ---------------------------------------------------------------------------
# AC5: orphan task with order only (missing result)
# ---------------------------------------------------------------------------


class TestOrderOnlyOrphan:
    def test_order_only_orphan_task_archived_without_error(self, tmp_path):
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        order, _ = _make_queue_files(queue_dir, UUID_C, make_result=False)
        _set_mtime(order, days_ago=10)
        _make_exec_jsonl(exec_md, [UUID_C])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_jsonl=exec_md,
            orphan_days=7,
            auto_repair=True,
        )

        assert res.archived_orphan == 1
        assert not order.exists()


# ---------------------------------------------------------------------------
# AC9: exec.md does not exist
# ---------------------------------------------------------------------------


class TestStderrKind:
    def test_gemini_stderr_matches_as_valid_kind(self, tmp_path):
        """gemini-stderr files match QUEUE_FILE_RE"""
        from ghdag.core.vocabulary import QUEUE_FILE_RE
        fname = f"{TS}-gemini-stderr-{UUID_C}.md"
        assert QUEUE_FILE_RE.match(fname) is not None
