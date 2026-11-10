"""Tests for ghdag.cleanup — AC1–AC10 and Issue-856 fix coverage."""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
from ghdag_cleanup_conftest_helpers import (
    FIXED_NOW_TS,
    UUID_A,
    UUID_D,
    _make_done_flag,
    _make_exec_jsonl,
    _make_queue_files,
    _set_mtime,
    _setup_dirs,
)

from ghdag.cleanup import cleanup_queue


class TestSweepExtras:
    def test_sweep_archives_old_slack_pending_json(self, tmp_path):
        """AC11: slack-pending-*.json files are swept"""
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        slack_file = queue_dir / f"slack-pending-{UUID_D}.json"
        slack_file.write_text("{}")
        _set_mtime(slack_file, days_ago=8)

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            orphan_days=7,
        )

        assert res.swept_extras == 1
        assert not slack_file.exists()
        # Must be moved under archive/YYYY-MM/extras/
        extras_dirs = list(archive_dir.glob("*/extras"))
        assert len(extras_dirs) == 1
        assert (extras_dirs[0] / slack_file.name).exists()

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
            exec_md=exec_md,
            orphan_days=7,
        )

        assert res.swept_extras == 1
        assert not nonstandard.exists()
        extras_dirs = list(archive_dir.glob("*/extras"))
        assert len(extras_dirs) == 1

    def test_sweep_skips_whitelist_files(self, tmp_path):
        """AC13: *.jsonl, .gitkeep, .ghdag.lock are whitelisted and not swept"""
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        exec_md.write_text("")  # actually create exec.jsonl
        gitkeep = queue_dir / ".gitkeep"
        gitkeep.touch()
        lock = queue_dir / ".ghdag.lock"
        lock.touch()
        audit = queue_dir / "audit.jsonl"
        audit.write_text("")

        for f in [exec_md, gitkeep, lock, audit]:
            _set_mtime(f, days_ago=30)

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            orphan_days=7,
        )

        assert res.swept_extras == 0
        assert exec_md.exists()
        assert gitkeep.exists()
        assert lock.exists()
        assert audit.exists()

    def test_sweep_skips_young_nonstandard_md(self, tmp_path):
        """AC14: files younger than orphan_days are not swept"""
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        slack_file = queue_dir / f"slack-pending-{UUID_D}.json"
        slack_file.write_text("{}")
        _set_mtime(slack_file, days_ago=3)

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            orphan_days=7,
        )

        assert res.swept_extras == 0
        assert slack_file.exists()

    def test_dry_run_sweep_leaves_files_in_place(self, tmp_path, capsys):
        """AC15: dry_run only lists sweep targets; files remain"""
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        slack_file = queue_dir / f"slack-pending-{UUID_D}.json"
        slack_file.write_text("{}")
        _set_mtime(slack_file, days_ago=8)

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            orphan_days=7,
            dry_run=True,
        )

        assert res.swept_extras == 1
        assert slack_file.exists()
        out = capsys.readouterr().out
        assert "[dry] sweep extras:" in out

    def test_sweep_skips_directories(self, tmp_path):
        """AC16: done/, archive/, thread-index/ directories are not swept"""
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        thread_index = queue_dir / "thread-index"
        thread_index.mkdir()

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            orphan_days=7,
        )

        assert res.swept_extras == 0
        assert done_dir.exists()
        assert archive_dir.exists()
        assert thread_index.exists()

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
            exec_md=exec_md,
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

    def test_sweep_skips_recently_updated_state_file(self, tmp_path):
        import os

        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        _make_exec_jsonl(exec_md, [])
        override = queue_dir / "quota-gate-override.json"
        override.write_text("{}")
        # birthtime = 8 days ago, mtime = 1 hour ago
        eight_days_ago = FIXED_NOW_TS - 8 * 86400
        one_hour_ago = FIXED_NOW_TS - 3600
        os.utime(override, (eight_days_ago, eight_days_ago))
        os.utime(override, (one_hour_ago, one_hour_ago))

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            orphan_days=7,
        )

        assert res.swept_extras == 0
        assert override.exists()

    def test_sweep_archives_old_timestamped_artifact(self, tmp_path):
        """AC-2: 14-digit-prefix file old enough by mtime is swept to extras."""
        import os

        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        _make_exec_jsonl(exec_md, [])
        artifact = queue_dir / "20260101120000-shell-result-abcd1234.md.prev-20260101T000000Z"
        artifact.write_text("prev")
        eight_days_ago = FIXED_NOW_TS - 8 * 86400
        os.utime(artifact, (eight_days_ago, eight_days_ago))

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            orphan_days=7,
        )

        assert res.swept_extras == 1
        assert not artifact.exists()
        extras_dirs = list(archive_dir.glob("*/extras"))
        assert len(extras_dirs) == 1

    def test_sweep_skips_lock_files(self, tmp_path):
        """AC-3: .lock files are not swept even when old."""
        import os

        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        _make_exec_jsonl(exec_md, [])
        lock1 = queue_dir / "quota-gate.json.lock"
        lock2 = queue_dir / "issuesmith-brake.json.lock"
        lock1.write_text("")
        lock2.write_text("")
        thirty_days_ago = FIXED_NOW_TS - 30 * 86400
        os.utime(lock1, (thirty_days_ago, thirty_days_ago))
        os.utime(lock2, (thirty_days_ago, thirty_days_ago))

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            orphan_days=7,
        )

        assert res.swept_extras == 0
        assert lock1.exists()
        assert lock2.exists()

    def test_sweep_skips_slack_pending_with_recent_mtime(self, tmp_path):
        """AC-5a: slack-pending-<uuid> created 8d ago but updated 1h ago is NOT swept."""
        import os

        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        _make_exec_jsonl(exec_md, [])
        slack_file = queue_dir / f"slack-pending-{UUID_D}.json"
        slack_file.write_text("{}")
        eight_days_ago = FIXED_NOW_TS - 8 * 86400
        one_hour_ago = FIXED_NOW_TS - 3600
        os.utime(slack_file, (eight_days_ago, eight_days_ago))
        os.utime(slack_file, (one_hour_ago, one_hour_ago))

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            orphan_days=7,
        )

        assert res.swept_extras == 0
        assert slack_file.exists()

    def test_sweep_archives_slack_pending_with_stale_mtime(self, tmp_path):
        """AC-5b: slack-pending-<uuid> created and not updated 8 days ago IS swept."""
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        _make_exec_jsonl(exec_md, [])
        slack_file = queue_dir / f"slack-pending-{UUID_D}.json"
        slack_file.write_text("{}")
        _set_mtime(slack_file, days_ago=8)

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            orphan_days=7,
        )

        assert res.swept_extras == 1
        assert not slack_file.exists()

    def test_dry_run_sweep_prints_dest_and_age(self, tmp_path, capsys):
        """AC-7: dry_run outputs [dry] sweep extras: <name> -> <dest> (token=..., age=...d)."""
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        _make_exec_jsonl(exec_md, [])
        slack_file = queue_dir / f"slack-pending-{UUID_D}.json"
        slack_file.write_text("{}")
        _set_mtime(slack_file, days_ago=8)

        cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            orphan_days=7,
            dry_run=True,
        )

        out = capsys.readouterr().out
        assert "[dry] sweep extras:" in out
        assert "token=" in out
        assert "age=" in out
        assert slack_file.exists()

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
            exec_md=exec_md,
            orphan_days=7,
            dry_run=True,
        )

        out = capsys.readouterr().out
        assert "age=8d)" in out
