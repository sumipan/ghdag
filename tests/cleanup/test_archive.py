"""Tests for ghdag.cleanup — AC1–AC10 and Issue-856 fix coverage."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

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

from ghdag.cleanup import cleanup_queue


class TestArchivedDone:
    def test_done_task_older_than_cutoff_is_archived(self, tmp_path):
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        order, result = _make_queue_files(queue_dir, UUID_A)
        _set_mtime(order, days_ago=2)
        _make_done_flag(done_dir, UUID_A)
        _make_exec_jsonl(exec_md, [UUID_A])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            cutoff_days=1,
        )

        assert res.archived_done == 1
        assert res.archived_orphan == 0
        assert res.pruned_exec == 1
        assert not order.exists()
        assert not result.exists()
        assert not (done_dir / UUID_A).exists()
        content = exec_md.read_text()
        assert UUID_A not in content

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
            exec_md=exec_md,
            cutoff_days=1,
        )

        assert res.archived_done == 0
        assert order.exists()
        assert result.exists()

    def test_archived_done_files_go_to_correct_subdir(self, tmp_path):
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        ts = "20260115100000"
        order, result = _make_queue_files(queue_dir, UUID_A, ts=ts)
        _set_mtime(order, days_ago=2)
        _make_done_flag(done_dir, UUID_A)
        _make_exec_jsonl(exec_md, [UUID_A])

        cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            cutoff_days=1,
        )

        dest_dir = archive_dir / "2026-01"
        assert (dest_dir / order.name).exists()
        assert (dest_dir / result.name).exists()


class TestArchivedOrphan:
    def test_orphan_task_older_than_orphan_days_is_archived(self, tmp_path):
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        order, result = _make_queue_files(queue_dir, UUID_B)
        _set_mtime(order, days_ago=10)
        _make_exec_jsonl(exec_md, [UUID_B])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            orphan_days=7,
            auto_repair=True,
        )

        assert res.archived_orphan == 1
        assert res.archived_done == 0
        assert res.pruned_exec == 1
        assert not order.exists()
        assert not result.exists()

    def test_orphan_files_go_to_orphan_subdir(self, tmp_path):
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        ts = "20260115100000"
        order, result = _make_queue_files(queue_dir, UUID_B, ts=ts)
        _set_mtime(order, days_ago=10)
        _make_exec_jsonl(exec_md, [UUID_B])

        cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            orphan_days=7,
            auto_repair=True,
        )

        dest_dir = archive_dir / "2026-01" / "orphan"
        assert (dest_dir / order.name).exists()
        assert (dest_dir / result.name).exists()

    def test_orphan_task_newer_than_orphan_days_is_not_archived(self, tmp_path):
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        order, result = _make_queue_files(queue_dir, UUID_B)
        _set_mtime(order, days_ago=3)
        _make_exec_jsonl(exec_md, [UUID_B])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            orphan_days=7,
        )

        assert res.archived_orphan == 0
        assert order.exists()


class TestResultOnlyDone:
    def test_result_only_done_task_archived_without_error(self, tmp_path):
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        _, result = _make_queue_files(queue_dir, UUID_C, make_order=False)
        _set_mtime(result, days_ago=2)
        _make_done_flag(done_dir, UUID_C)
        _make_exec_jsonl(exec_md, [UUID_C])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            cutoff_days=1,
        )

        assert res.archived_done == 1
        assert not result.exists()


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
            exec_md=exec_md,
            orphan_days=7,
            auto_repair=True,
        )

        assert res.archived_orphan == 1
        assert not order.exists()


class TestBoundaryValues:
    def test_exactly_cutoff_days_is_archived(self, tmp_path):
        """Tasks exactly at cutoff_days are archived (<=)"""
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        order, _ = _make_queue_files(queue_dir, UUID_A)
        _make_done_flag(done_dir, UUID_A)
        _make_exec_jsonl(exec_md, [UUID_A])

        # Mock file_timestamp to a fixed value to test cutoff_ts == file_ts boundary
        fixed_ts = 1_700_000_000.0  # fixed value
        fake_now = datetime.fromtimestamp(fixed_ts + 86400, tz=timezone.utc)

        with patch("ghdag.cleanup.orchestrator.datetime") as mock_dt, \
             patch("ghdag.cleanup.orchestrator.file_timestamp", return_value=fixed_ts):
            mock_dt.now.return_value = fake_now
            mock_dt.fromtimestamp = datetime.fromtimestamp

            res = cleanup_queue(
                queue_dir=queue_dir,
                archive_dir=archive_dir,
                done_dir=done_dir,
                exec_md=exec_md,
                cutoff_days=1,
            )

        assert res.archived_done == 1


# ---------------------------------------------------------------------------
# AC9: exec.md does not exist
# ---------------------------------------------------------------------------


class TestExecMdMissing:
    def test_missing_exec_md_skips_pruning_but_archives(self, tmp_path):
        queue_dir, archive_dir, done_dir, _ = _setup_dirs(tmp_path)
        exec_md = queue_dir / "exec.jsonl"  # does not exist
        order, _ = _make_queue_files(queue_dir, UUID_A)
        _set_mtime(order, days_ago=2)
        _make_done_flag(done_dir, UUID_A)

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            cutoff_days=1,
        )

        assert res.archived_done == 1
        assert res.pruned_exec == 0
        assert not order.exists()


class TestUUIDCaseInsensitive:
    def test_uppercase_uuid_in_filename_matches_lowercase_done_flag(self, tmp_path):
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        uuid_upper = UUID_A.upper()
        order, _ = _make_queue_files(queue_dir, uuid_upper)
        _set_mtime(order, days_ago=2)
        _make_done_flag(done_dir, UUID_A.lower())
        _make_exec_jsonl(exec_md, [UUID_A.lower()])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            cutoff_days=1,
        )

        assert res.archived_done == 1


class TestCompoundToolName:
    def test_compound_tool_name_done_task_is_archived(self, tmp_path):
        """Compound tool-name files like claude-investigator are archived when completed"""
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        # Create files with a compound tool name
        order = queue_dir / f"{TS}-claude-investigator-order-{UUID_A}.md"
        result = queue_dir / f"{TS}-claude-investigator-result-{UUID_A}.md"
        order.write_text("order")
        result.write_text("result")
        _set_mtime(order, days_ago=2)
        _make_done_flag(done_dir, UUID_A)
        _make_exec_jsonl(exec_md, [UUID_A])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            cutoff_days=1,
        )

        assert res.archived_done == 1
        assert not order.exists()
        assert not result.exists()

    def test_gemini_redelegator_done_task_is_archived(self, tmp_path):
        """Compound tool-name files like gemini-redelegator are archived when completed"""
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        order = queue_dir / f"{TS}-gemini-redelegator-order-{UUID_B}.md"
        order.write_text("order")
        _set_mtime(order, days_ago=2)
        _make_done_flag(done_dir, UUID_B)
        _make_exec_jsonl(exec_md, [UUID_B])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            cutoff_days=1,
        )

        assert res.archived_done == 1
        assert not order.exists()

    def test_cursor_investigator_orphan_task_is_archived(self, tmp_path):
        """Orphan tasks with compound tool names like cursor-investigator are archived"""
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        order = queue_dir / f"{TS}-cursor-investigator-order-{UUID_C}.md"
        order.write_text("order")
        _set_mtime(order, days_ago=10)
        _make_exec_jsonl(exec_md, [UUID_C])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            orphan_days=7,
            auto_repair=True,
        )

        assert res.archived_orphan == 1
        assert not order.exists()


class TestStderrKind:
    def test_stderr_file_done_task_is_archived(self, tmp_path):
        """claude-stderr stderr files are archived as completed"""
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        stderr_file = queue_dir / f"{TS}-claude-stderr-{UUID_A}.md"
        stderr_file.write_text("stderr content")
        _set_mtime(stderr_file, days_ago=2)
        _make_done_flag(done_dir, UUID_A)
        _make_exec_jsonl(exec_md, [UUID_A])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            cutoff_days=1,
        )

        assert res.archived_done == 1
        assert not stderr_file.exists()

    def test_cursor_stderr_orphan_task_is_archived(self, tmp_path):
        """cursor-stderr orphan tasks are archived"""
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        stderr_file = queue_dir / f"{TS}-cursor-stderr-{UUID_B}.md"
        stderr_file.write_text("stderr content")
        _set_mtime(stderr_file, days_ago=10)
        _make_exec_jsonl(exec_md, [UUID_B])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            orphan_days=7,
            auto_repair=True,
        )

        assert res.archived_orphan == 1
        assert not stderr_file.exists()

    def test_gemini_stderr_matches_as_valid_kind(self, tmp_path):
        """gemini-stderr files match QUEUE_FILE_RE"""
        from ghdag.cleanup import QUEUE_FILE_RE
        fname = f"{TS}-gemini-stderr-{UUID_C}.md"
        assert QUEUE_FILE_RE.match(fname) is not None


class TestOrphanDoneMark:
    def test_orphan_archive_creates_done_marker(self, tmp_path):
        """Create an ORPHAN_ARCHIVED done marker before orphan archive"""
        queue_dir, archive_dir, done_dir, exec_jsonl = _setup_dirs(tmp_path)
        order, _ = _make_queue_files(queue_dir, UUID_B)
        _set_mtime(order, days_ago=10)
        _make_exec_jsonl(exec_jsonl, [UUID_B])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_jsonl,
            orphan_days=7,
            auto_repair=True,
        )

        assert res.archived_orphan == 1
        done_flag = done_dir / UUID_B
        assert done_flag.exists()
        assert "ORPHAN_ARCHIVED" in done_flag.read_text()

    def test_orphan_done_marker_created_before_file_move(self, tmp_path):
        """done marker is created before file moves (so DagEngine does not misread state)"""
        queue_dir, archive_dir, done_dir, exec_jsonl = _setup_dirs(tmp_path)
        order, _ = _make_queue_files(queue_dir, UUID_B)
        _set_mtime(order, days_ago=10)
        _make_exec_jsonl(exec_jsonl, [UUID_B])

        creation_log: list[str] = []

        original_rename = Path.rename

        def tracking_rename(self, target):
            creation_log.append(f"rename:{self.name}")
            return original_rename(self, target)

        with patch.object(Path, "rename", tracking_rename):
            with patch("builtins.open", wraps=open):
                cleanup_queue(
                    queue_dir=queue_dir,
                    archive_dir=archive_dir,
                    done_dir=done_dir,
                    exec_md=exec_jsonl,
                    orphan_days=7,
                    auto_repair=True,
                )

        done_flag = done_dir / UUID_B
        assert done_flag.exists()
