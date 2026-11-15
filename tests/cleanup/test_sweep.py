"""Tests for ghdag.cleanup — AC1–AC10 and Issue-856 fix coverage."""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
from ghdag_cleanup_conftest_helpers import (
    UUID_A,
    UUID_D,
    _make_done_flag,
    _make_exec_jsonl,
    _make_queue_files,
    _set_mtime,
    _setup_dirs,
)

from ghdag.cleanup.orchestrator import cleanup_queue


class TestSweepExtras:

    def test_sweep_archives_old_nonstandard_md(self, tmp_path):
        """AC12: non-standard-named .md files are swept"""
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        nonstandard = queue_dir / "20260324105832-gemini-deep-kakiuchi-result.md"
        nonstandard.write_text("result")
        _set_mtime(nonstandard, days_ago=8)

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_jsonl=exec_md,
            orphan_days=7,
        )

        assert res.swept_extras == 1
        assert not nonstandard.exists()
        extras_dirs = list(archive_dir.glob("*/extras"))
        assert len(extras_dirs) == 1


    def test_sweep_skips_jobs_already_archived_by_primary_cleanup(self, tmp_path):
        """AC17: files archived in Phase 1 are not double-processed in Phase 2"""
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        order, result = _make_queue_files(queue_dir, UUID_A)
        _set_mtime(order, days_ago=2)
        _make_done_flag(done_dir, UUID_A)

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_jsonl=exec_md,
            cutoff_days=1,
            orphan_days=7,
        )

        assert res.archived_done == 1
        assert res.swept_extras == 0


class TestIsSweepableExtra:
    """Unit tests for is_sweepable_extra token detection."""

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("gdrive-sync-20260917-210444.md", "20260917"),
            ("research-3399-13605dbe-retry-focus.json", "13605dbe"),
            ("20260101120000-x-result.md", "20260101120000"),
            ("slack-pending-dddddddd-dddd-dddd-dddd-dddddddddddd.json", "dddddddd"),
            ("quota-gate.json", None),
            ("engine-stall-notified.json", None),
            ("issuesmith-brake.json", None),
            (".ghdag-release-state.json", None),
            ("news_batch.json", None),
            ("quota-gate.json.lock", None),
            ("audit.jsonl", None),
            (".gitkeep", None),
        ],
    )
    def test_parametrized(self, name, expected):
        from ghdag.cleanup.orchestrator import is_sweepable_extra

        result = is_sweepable_extra(name)
        assert result == expected


class TestPhase3MtimeSweep:
    """AC-1: quota-gate-override.json created 8 days ago, updated 1 hour ago is NOT swept."""


    def test_dry_run_sweep_age_is_measured_from_fixed_now(self, tmp_path, capsys):
        """Sweep dry-run age reflects days elapsed from the frozen orchestrator clock."""
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        _make_exec_jsonl(exec_md, [])
        slack_file = queue_dir / f"slack-pending-{UUID_D}.json"
        slack_file.write_text("{}")
        _set_mtime(slack_file, days_ago=8)

        cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_jsonl=exec_md,
            orphan_days=7,
            dry_run=True,
        )

        out = capsys.readouterr().out
        assert "age=8d)" in out
