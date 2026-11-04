"""Tests for ghdag.cleanup — AC1–AC10 and Issue-856 fix coverage."""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
from ghdag_cleanup_conftest_helpers import (
    UUID_A,
    UUID_B,
    _make_done_flag,
    _make_exec_jsonl,
    _make_queue_files,
    _set_mtime,
    _setup_dirs,
)

from ghdag.cleanup import cleanup_queue


class TestDryRun:
    def test_dry_run_makes_no_changes(self, tmp_path, capsys):
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        order, result = _make_queue_files(queue_dir, UUID_A)
        _set_mtime(order, days_ago=2)
        _make_done_flag(done_dir, UUID_A)
        _make_exec_jsonl(exec_md, [UUID_A])
        exec_md_content_before = exec_md.read_text()

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            cutoff_days=1,
            dry_run=True,
        )

        assert order.exists()
        assert result.exists()
        assert (done_dir / UUID_A).exists()
        assert exec_md.read_text() == exec_md_content_before
        assert res.archived_done == 1

    def test_dry_run_outputs_to_stdout(self, tmp_path, capsys):
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        order, _ = _make_queue_files(queue_dir, UUID_A)
        _set_mtime(order, days_ago=2)
        _make_done_flag(done_dir, UUID_A)
        _make_exec_jsonl(exec_md, [UUID_A])

        cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            cutoff_days=1,
            dry_run=True,
        )

        out = capsys.readouterr().out
        assert "[dry]" in out
class TestQueueDirMissing:
    def test_missing_queue_dir_exits_1(self, tmp_path, capsys):
        queue_dir = tmp_path / "nonexistent_queue"
        archive_dir = tmp_path / "jobs" / "archive"
        done_dir = tmp_path / "jobs" / "done"
        exec_md = tmp_path / "exec.jsonl"
        archive_dir.mkdir(parents=True)
        done_dir.mkdir(parents=True)

        with pytest.raises(SystemExit) as exc:
            cleanup_queue(
                queue_dir=queue_dir,
                archive_dir=archive_dir,
                done_dir=done_dir,
                exec_md=exec_md,
            )
        assert exc.value.code == 1
        assert capsys.readouterr().err != ""


# ---------------------------------------------------------------------------
# AC7: zero matching files
# ---------------------------------------------------------------------------


class TestNoMatchingFiles:
    def test_empty_queue_dir_returns_zero_counts(self, tmp_path):
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        _make_exec_jsonl(exec_md, [])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
        )

        assert res.archived_done == 0
        assert res.archived_orphan == 0
        assert res.pruned_exec == 0
class TestAutoRepairFalse:
    def test_case_d_no_archive_detected_orphan(self, tmp_path, capsys):
        """auto_repair=False: Case D orphan is detect-only, no file move, detected_orphan=1"""
        queue_dir, archive_dir, done_dir, exec_jsonl = _setup_dirs(tmp_path)
        order, result = _make_queue_files(queue_dir, UUID_A)
        _set_mtime(order, days_ago=10)
        _make_exec_jsonl(exec_jsonl, [UUID_A])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_jsonl,
            orphan_days=7,
            auto_repair=False,
        )

        assert res.archived_orphan == 0
        assert res.detected_orphan == 1
        assert res.pruned_exec == 0
        assert order.exists()
        assert result.exists()
        assert UUID_A in exec_jsonl.read_text()

    def test_case_d_stderr_report_orphan(self, tmp_path, capsys):
        """auto_repair=False: Case D → ORPHAN detected report on stderr"""
        queue_dir, archive_dir, done_dir, exec_jsonl = _setup_dirs(tmp_path)
        order, _ = _make_queue_files(queue_dir, UUID_A)
        _set_mtime(order, days_ago=10)
        _make_exec_jsonl(exec_jsonl, [UUID_A])

        cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_jsonl,
            orphan_days=7,
            auto_repair=False,
        )

        err = capsys.readouterr().err
        assert "ORPHAN detected" in err
        assert UUID_A in err
        assert "--auto-repair" in err

    def test_case_d_no_done_marker_created(self, tmp_path):
        """auto_repair=False: Case D → done marker is not created"""
        queue_dir, archive_dir, done_dir, exec_jsonl = _setup_dirs(tmp_path)
        order, _ = _make_queue_files(queue_dir, UUID_A)
        _set_mtime(order, days_ago=10)
        _make_exec_jsonl(exec_jsonl, [UUID_A])

        cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_jsonl,
            orphan_days=7,
            auto_repair=False,
        )

        assert not (done_dir / UUID_A).exists()

    def test_case_f_no_prune_detected_dead(self, tmp_path, capsys):
        """auto_repair=False: Case F dead entry is detect-only, no exec line delete, detected_dead=1"""
        queue_dir, archive_dir, done_dir, exec_jsonl = _setup_dirs(tmp_path)
        _make_exec_jsonl(exec_jsonl, [UUID_B])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_jsonl,
            auto_repair=False,
        )

        assert res.detected_dead == 1
        assert res.pruned_exec == 0
        assert UUID_B in exec_jsonl.read_text()

    def test_case_f_stderr_report_dead(self, tmp_path, capsys):
        """auto_repair=False: Case F → DEAD_ENTRY detected report on stderr"""
        queue_dir, archive_dir, done_dir, exec_jsonl = _setup_dirs(tmp_path)
        _make_exec_jsonl(exec_jsonl, [UUID_B])

        cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_jsonl,
            auto_repair=False,
        )

        err = capsys.readouterr().err
        assert "DEAD_ENTRY detected" in err
        assert UUID_B in err
        assert "--auto-repair" in err

    def test_auto_repair_true_case_d_archives(self, tmp_path):
        """auto_repair=True: Case D → orphan archived as before"""
        queue_dir, archive_dir, done_dir, exec_jsonl = _setup_dirs(tmp_path)
        order, result = _make_queue_files(queue_dir, UUID_A)
        _set_mtime(order, days_ago=10)
        _make_exec_jsonl(exec_jsonl, [UUID_A])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_jsonl,
            orphan_days=7,
            auto_repair=True,
        )

        assert res.archived_orphan == 1
        assert res.detected_orphan == 0
        assert not order.exists()
        assert not result.exists()

    def test_auto_repair_true_case_f_prunes(self, tmp_path):
        """auto_repair=True: Case F → exec line deleted as before"""
        queue_dir, archive_dir, done_dir, exec_jsonl = _setup_dirs(tmp_path)
        _make_exec_jsonl(exec_jsonl, [UUID_B])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_jsonl,
            auto_repair=True,
        )

        assert res.pruned_exec == 1
        assert res.detected_dead == 0
        assert UUID_B not in exec_jsonl.read_text()

    def test_auto_repair_false_dry_run_no_dry_prefix_in_stderr(self, tmp_path, capsys):
        """auto_repair=False, dry_run=True: Case D/F detect reports have no [dry] prefix"""
        queue_dir, archive_dir, done_dir, exec_jsonl = _setup_dirs(tmp_path)
        order, _ = _make_queue_files(queue_dir, UUID_A)
        _set_mtime(order, days_ago=10)
        _make_exec_jsonl(exec_jsonl, [UUID_A])

        cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_jsonl,
            orphan_days=7,
            auto_repair=False,
            dry_run=True,
        )

        err = capsys.readouterr().err
        assert "ORPHAN detected" in err
        assert "[dry]" not in err
