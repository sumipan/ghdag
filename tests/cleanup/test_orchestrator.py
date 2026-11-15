"""Tests for ghdag.cleanup — AC1–AC10 and Issue-856 fix coverage."""
from __future__ import annotations

import pytest
from ghdag_cleanup_conftest_helpers import (
    UUID_A,
    UUID_B,
    _make_done_flag,
    _make_exec_jsonl,
    _make_queue_files,
    _set_mtime,
    _setup_dirs,
)

from ghdag.cleanup.orchestrator import cleanup_queue


class TestDryRun:

    def test_dry_run_outputs_to_stdout(self, tmp_path, capsys):
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        order, _ = _make_queue_files(queue_dir, UUID_A)
        _set_mtime(order, days_ago=2)
        _make_done_flag(done_dir, UUID_A)
        _make_exec_jsonl(exec_md, [UUID_A])
        cleanup_queue(queue_dir=queue_dir, archive_dir=archive_dir, done_dir=done_dir, exec_jsonl=exec_md, cutoff_days=1, dry_run=True)
        out = capsys.readouterr().out
        assert '[dry]' in out

class TestQueueDirMissing:

    def test_missing_queue_dir_exits_1(self, tmp_path, capsys):
        queue_dir = tmp_path / 'nonexistent_queue'
        archive_dir = tmp_path / 'jobs' / 'archive'
        done_dir = tmp_path / 'jobs' / 'done'
        exec_md = tmp_path / 'exec.jsonl'
        archive_dir.mkdir(parents=True)
        done_dir.mkdir(parents=True)
        with pytest.raises(SystemExit) as exc:
            cleanup_queue(queue_dir=queue_dir, archive_dir=archive_dir, done_dir=done_dir, exec_jsonl=exec_md)
        assert exc.value.code == 1
        assert capsys.readouterr().err != ''

class TestAutoRepairFalse:

    def test_case_d_no_done_marker_created(self, tmp_path):
        """auto_repair=False: Case D → done marker is not created"""
        queue_dir, archive_dir, done_dir, exec_jsonl = _setup_dirs(tmp_path)
        order, _ = _make_queue_files(queue_dir, UUID_A)
        _set_mtime(order, days_ago=10)
        _make_exec_jsonl(exec_jsonl, [UUID_A])
        cleanup_queue(queue_dir=queue_dir, archive_dir=archive_dir, done_dir=done_dir, exec_jsonl=exec_jsonl, orphan_days=7, auto_repair=False)
        assert not (done_dir / UUID_A).exists()

    def test_case_f_no_prune_detected_dead(self, tmp_path, capsys):
        """auto_repair=False: Case F dead entry is detect-only, no exec line delete, detected_dead=1"""
        queue_dir, archive_dir, done_dir, exec_jsonl = _setup_dirs(tmp_path)
        _make_exec_jsonl(exec_jsonl, [UUID_B])
        res = cleanup_queue(queue_dir=queue_dir, archive_dir=archive_dir, done_dir=done_dir, exec_jsonl=exec_jsonl, auto_repair=False)
        assert res.detected_dead == 1
        assert res.pruned_exec == 0
        assert UUID_B in exec_jsonl.read_text()

    def test_auto_repair_true_case_f_prunes(self, tmp_path):
        """auto_repair=True: Case F → exec line deleted as before"""
        queue_dir, archive_dir, done_dir, exec_jsonl = _setup_dirs(tmp_path)
        _make_exec_jsonl(exec_jsonl, [UUID_B])
        res = cleanup_queue(queue_dir=queue_dir, archive_dir=archive_dir, done_dir=done_dir, exec_jsonl=exec_jsonl, auto_repair=True)
        assert res.pruned_exec == 1
        assert res.detected_dead == 0
        assert UUID_B not in exec_jsonl.read_text()
