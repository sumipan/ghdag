"""Tests for ghdag.cleanup — AC1〜AC10 に対応する単体テスト。"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from ghdag.cleanup import CleanupResult, cleanup_queue, file_timestamp

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UUID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
UUID_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
UUID_C = "cccccccc-cccc-cccc-cccc-cccccccccccc"
TS = "20260101120000"


def _make_queue_files(
    queue_dir: Path,
    uuid: str,
    ts: str = TS,
    tool: str = "claude",
    make_order: bool = True,
    make_result: bool = True,
) -> tuple[Path | None, Path | None]:
    order = result = None
    if make_order:
        order = queue_dir / f"{ts}-{tool}-order-{uuid}.md"
        order.write_text(f"order content for {uuid}")
    if make_result:
        result = queue_dir / f"{ts}-{tool}-result-{uuid}.md"
        result.write_text(f"result content for {uuid}")
    return order, result


def _set_mtime(path: Path, days_ago: float) -> None:
    import os
    t = time.time() - days_ago * 86400
    os.utime(path, (t, t))


def _make_exec_md(exec_md: Path, entries: list[str]) -> None:
    lines = [f"{uuid}: cat queue/order.md | claude\n" for uuid in entries]
    exec_md.write_text("".join(lines), encoding="utf-8")


def _make_exec_done_flag(exec_done_dir: Path, uuid: str) -> None:
    exec_done_dir.mkdir(parents=True, exist_ok=True)
    (exec_done_dir / uuid).touch()


def _setup_dirs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    queue_dir = tmp_path / "queue"
    queue_done_dir = tmp_path / "queue-done"
    exec_done_dir = tmp_path / "exec-done"
    exec_md = queue_dir / "exec.md"
    queue_dir.mkdir()
    queue_done_dir.mkdir()
    exec_done_dir.mkdir()
    return queue_dir, queue_done_dir, exec_done_dir, exec_md


# ---------------------------------------------------------------------------
# file_timestamp
# ---------------------------------------------------------------------------


class TestFileTimestamp:
    def test_returns_float(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("x")
        ts = file_timestamp(f)
        assert isinstance(ts, float)

    def test_prefers_birthtime_when_available(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("x")
        fake_stat = type("Stat", (), {"st_birthtime": 1_000_000.0, "st_mtime": 2_000_000.0})()
        with patch.object(Path, "stat", return_value=fake_stat):
            ts = file_timestamp(f)
        assert ts == 1_000_000.0

    def test_fallbacks_to_mtime_when_no_birthtime(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("x")
        fake_stat = type("Stat", (), {"st_mtime": 2_000_000.0})()
        with patch.object(Path, "stat", return_value=fake_stat):
            ts = file_timestamp(f)
        assert ts == 2_000_000.0


# ---------------------------------------------------------------------------
# AC1: 完了済みタスクのアーカイブ
# ---------------------------------------------------------------------------


class TestArchivedDone:
    def test_done_task_older_than_cutoff_is_archived(self, tmp_path):
        queue_dir, queue_done_dir, exec_done_dir, exec_md = _setup_dirs(tmp_path)
        order, result = _make_queue_files(queue_dir, UUID_A)
        _set_mtime(order, days_ago=2)
        _make_exec_done_flag(exec_done_dir, UUID_A)
        _make_exec_md(exec_md, [UUID_A])

        res = cleanup_queue(
            queue_dir=queue_dir,
            queue_done_dir=queue_done_dir,
            exec_done_dir=exec_done_dir,
            exec_md=exec_md,
            cutoff_days=1,
        )

        assert res.archived_done == 1
        assert res.archived_orphan == 0
        assert res.pruned_exec == 1
        assert not order.exists()
        assert not result.exists()
        assert not (exec_done_dir / UUID_A).exists()
        content = exec_md.read_text()
        assert UUID_A not in content

    def test_done_task_newer_than_cutoff_is_not_archived(self, tmp_path):
        queue_dir, queue_done_dir, exec_done_dir, exec_md = _setup_dirs(tmp_path)
        order, result = _make_queue_files(queue_dir, UUID_A)
        _set_mtime(order, days_ago=0.5)
        _make_exec_done_flag(exec_done_dir, UUID_A)
        _make_exec_md(exec_md, [UUID_A])

        res = cleanup_queue(
            queue_dir=queue_dir,
            queue_done_dir=queue_done_dir,
            exec_done_dir=exec_done_dir,
            exec_md=exec_md,
            cutoff_days=1,
        )

        assert res.archived_done == 0
        assert order.exists()
        assert result.exists()

    def test_archived_done_files_go_to_correct_subdir(self, tmp_path):
        queue_dir, queue_done_dir, exec_done_dir, exec_md = _setup_dirs(tmp_path)
        ts = "20260115100000"
        order, result = _make_queue_files(queue_dir, UUID_A, ts=ts)
        _set_mtime(order, days_ago=2)
        _make_exec_done_flag(exec_done_dir, UUID_A)
        _make_exec_md(exec_md, [UUID_A])

        cleanup_queue(
            queue_dir=queue_dir,
            queue_done_dir=queue_done_dir,
            exec_done_dir=exec_done_dir,
            exec_md=exec_md,
            cutoff_days=1,
        )

        dest_dir = queue_done_dir / "2026-01"
        assert (dest_dir / order.name).exists()
        assert (dest_dir / result.name).exists()


# ---------------------------------------------------------------------------
# AC2: 孤立タスクのアーカイブ
# ---------------------------------------------------------------------------


class TestArchivedOrphan:
    def test_orphan_task_older_than_orphan_days_is_archived(self, tmp_path):
        queue_dir, queue_done_dir, exec_done_dir, exec_md = _setup_dirs(tmp_path)
        order, result = _make_queue_files(queue_dir, UUID_B)
        _set_mtime(order, days_ago=10)
        _make_exec_md(exec_md, [UUID_B])

        res = cleanup_queue(
            queue_dir=queue_dir,
            queue_done_dir=queue_done_dir,
            exec_done_dir=exec_done_dir,
            exec_md=exec_md,
            orphan_days=7,
        )

        assert res.archived_orphan == 1
        assert res.archived_done == 0
        assert res.pruned_exec == 1
        assert not order.exists()
        assert not result.exists()

    def test_orphan_files_go_to_orphan_subdir(self, tmp_path):
        queue_dir, queue_done_dir, exec_done_dir, exec_md = _setup_dirs(tmp_path)
        ts = "20260115100000"
        order, result = _make_queue_files(queue_dir, UUID_B, ts=ts)
        _set_mtime(order, days_ago=10)
        _make_exec_md(exec_md, [UUID_B])

        cleanup_queue(
            queue_dir=queue_dir,
            queue_done_dir=queue_done_dir,
            exec_done_dir=exec_done_dir,
            exec_md=exec_md,
            orphan_days=7,
        )

        dest_dir = queue_done_dir / "2026-01" / "orphan"
        assert (dest_dir / order.name).exists()
        assert (dest_dir / result.name).exists()

    def test_orphan_task_newer_than_orphan_days_is_not_archived(self, tmp_path):
        queue_dir, queue_done_dir, exec_done_dir, exec_md = _setup_dirs(tmp_path)
        order, result = _make_queue_files(queue_dir, UUID_B)
        _set_mtime(order, days_ago=3)
        _make_exec_md(exec_md, [UUID_B])

        res = cleanup_queue(
            queue_dir=queue_dir,
            queue_done_dir=queue_done_dir,
            exec_done_dir=exec_done_dir,
            exec_md=exec_md,
            orphan_days=7,
        )

        assert res.archived_orphan == 0
        assert order.exists()


# ---------------------------------------------------------------------------
# AC3: dry_run
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_makes_no_changes(self, tmp_path, capsys):
        queue_dir, queue_done_dir, exec_done_dir, exec_md = _setup_dirs(tmp_path)
        order, result = _make_queue_files(queue_dir, UUID_A)
        _set_mtime(order, days_ago=2)
        _make_exec_done_flag(exec_done_dir, UUID_A)
        _make_exec_md(exec_md, [UUID_A])
        exec_md_content_before = exec_md.read_text()

        res = cleanup_queue(
            queue_dir=queue_dir,
            queue_done_dir=queue_done_dir,
            exec_done_dir=exec_done_dir,
            exec_md=exec_md,
            cutoff_days=1,
            dry_run=True,
        )

        assert order.exists()
        assert result.exists()
        assert (exec_done_dir / UUID_A).exists()
        assert exec_md.read_text() == exec_md_content_before
        assert res.archived_done == 1

    def test_dry_run_outputs_to_stdout(self, tmp_path, capsys):
        queue_dir, queue_done_dir, exec_done_dir, exec_md = _setup_dirs(tmp_path)
        order, _ = _make_queue_files(queue_dir, UUID_A)
        _set_mtime(order, days_ago=2)
        _make_exec_done_flag(exec_done_dir, UUID_A)
        _make_exec_md(exec_md, [UUID_A])

        cleanup_queue(
            queue_dir=queue_dir,
            queue_done_dir=queue_done_dir,
            exec_done_dir=exec_done_dir,
            exec_md=exec_md,
            cutoff_days=1,
            dry_run=True,
        )

        out = capsys.readouterr().out
        assert "[dry]" in out


# ---------------------------------------------------------------------------
# AC4: result のみ存在（order 欠損）の完了済みタスク
# ---------------------------------------------------------------------------


class TestResultOnlyDone:
    def test_result_only_done_task_archived_without_error(self, tmp_path):
        queue_dir, queue_done_dir, exec_done_dir, exec_md = _setup_dirs(tmp_path)
        _, result = _make_queue_files(queue_dir, UUID_C, make_order=False)
        _set_mtime(result, days_ago=2)
        _make_exec_done_flag(exec_done_dir, UUID_C)
        _make_exec_md(exec_md, [UUID_C])

        res = cleanup_queue(
            queue_dir=queue_dir,
            queue_done_dir=queue_done_dir,
            exec_done_dir=exec_done_dir,
            exec_md=exec_md,
            cutoff_days=1,
        )

        assert res.archived_done == 1
        assert not result.exists()


# ---------------------------------------------------------------------------
# AC5: order のみ存在（result 欠損）の孤立タスク
# ---------------------------------------------------------------------------


class TestOrderOnlyOrphan:
    def test_order_only_orphan_task_archived_without_error(self, tmp_path):
        queue_dir, queue_done_dir, exec_done_dir, exec_md = _setup_dirs(tmp_path)
        order, _ = _make_queue_files(queue_dir, UUID_C, make_result=False)
        _set_mtime(order, days_ago=10)
        _make_exec_md(exec_md, [UUID_C])

        res = cleanup_queue(
            queue_dir=queue_dir,
            queue_done_dir=queue_done_dir,
            exec_done_dir=exec_done_dir,
            exec_md=exec_md,
            orphan_days=7,
        )

        assert res.archived_orphan == 1
        assert not order.exists()


# ---------------------------------------------------------------------------
# AC6: queue/ が存在しない
# ---------------------------------------------------------------------------


class TestQueueDirMissing:
    def test_missing_queue_dir_exits_1(self, tmp_path, capsys):
        queue_dir = tmp_path / "nonexistent_queue"
        queue_done_dir = tmp_path / "queue-done"
        exec_done_dir = tmp_path / "exec-done"
        exec_md = tmp_path / "exec.md"
        queue_done_dir.mkdir()
        exec_done_dir.mkdir()

        with pytest.raises(SystemExit) as exc:
            cleanup_queue(
                queue_dir=queue_dir,
                queue_done_dir=queue_done_dir,
                exec_done_dir=exec_done_dir,
                exec_md=exec_md,
            )
        assert exc.value.code == 1
        assert capsys.readouterr().err != ""


# ---------------------------------------------------------------------------
# AC7: マッチするファイルが 0 件
# ---------------------------------------------------------------------------


class TestNoMatchingFiles:
    def test_empty_queue_dir_returns_zero_counts(self, tmp_path):
        queue_dir, queue_done_dir, exec_done_dir, exec_md = _setup_dirs(tmp_path)
        _make_exec_md(exec_md, [])

        res = cleanup_queue(
            queue_dir=queue_dir,
            queue_done_dir=queue_done_dir,
            exec_done_dir=exec_done_dir,
            exec_md=exec_md,
        )

        assert res.archived_done == 0
        assert res.archived_orphan == 0
        assert res.pruned_exec == 0


# ---------------------------------------------------------------------------
# AC8: 境界値（ちょうど cutoff 日数）
# ---------------------------------------------------------------------------


class TestBoundaryValues:
    def test_exactly_cutoff_days_is_archived(self, tmp_path):
        """cutoff_days ちょうどのタスクはアーカイブされる（<=）"""
        queue_dir, queue_done_dir, exec_done_dir, exec_md = _setup_dirs(tmp_path)
        order, _ = _make_queue_files(queue_dir, UUID_A)
        _make_exec_done_flag(exec_done_dir, UUID_A)
        _make_exec_md(exec_md, [UUID_A])

        # file_timestamp を固定値で mock し、cutoff_ts == file_ts の境界をテスト
        fixed_ts = 1_700_000_000.0  # 固定値
        fake_now = datetime.fromtimestamp(fixed_ts + 86400, tz=timezone.utc)

        with patch("ghdag.cleanup.datetime") as mock_dt, \
             patch("ghdag.cleanup.file_timestamp", return_value=fixed_ts):
            mock_dt.now.return_value = fake_now
            mock_dt.fromtimestamp = datetime.fromtimestamp

            res = cleanup_queue(
                queue_dir=queue_dir,
                queue_done_dir=queue_done_dir,
                exec_done_dir=exec_done_dir,
                exec_md=exec_md,
                cutoff_days=1,
            )

        assert res.archived_done == 1


# ---------------------------------------------------------------------------
# AC9: exec.md が存在しない
# ---------------------------------------------------------------------------


class TestExecMdMissing:
    def test_missing_exec_md_skips_pruning_but_archives(self, tmp_path):
        queue_dir, queue_done_dir, exec_done_dir, _ = _setup_dirs(tmp_path)
        exec_md = queue_dir / "exec.md"  # does not exist
        order, _ = _make_queue_files(queue_dir, UUID_A)
        _set_mtime(order, days_ago=2)
        _make_exec_done_flag(exec_done_dir, UUID_A)

        res = cleanup_queue(
            queue_dir=queue_dir,
            queue_done_dir=queue_done_dir,
            exec_done_dir=exec_done_dir,
            exec_md=exec_md,
            cutoff_days=1,
        )

        assert res.archived_done == 1
        assert res.pruned_exec == 0
        assert not order.exists()


# ---------------------------------------------------------------------------
# AC10: UUID の大文字・小文字混在
# ---------------------------------------------------------------------------


class TestUUIDCaseInsensitive:
    def test_uppercase_uuid_in_filename_matches_lowercase_exec_done_flag(self, tmp_path):
        queue_dir, queue_done_dir, exec_done_dir, exec_md = _setup_dirs(tmp_path)
        uuid_upper = UUID_A.upper()
        order, _ = _make_queue_files(queue_dir, uuid_upper)
        _set_mtime(order, days_ago=2)
        _make_exec_done_flag(exec_done_dir, UUID_A.lower())
        _make_exec_md(exec_md, [UUID_A.lower()])

        res = cleanup_queue(
            queue_dir=queue_dir,
            queue_done_dir=queue_done_dir,
            exec_done_dir=exec_done_dir,
            exec_md=exec_md,
            cutoff_days=1,
        )

        assert res.archived_done == 1


# ---------------------------------------------------------------------------
# CleanupResult dataclass
# ---------------------------------------------------------------------------


class TestCleanupResult:
    def test_result_fields(self):
        r = CleanupResult(archived_done=1, archived_orphan=2, pruned_exec=3)
        assert r.archived_done == 1
        assert r.archived_orphan == 2
        assert r.pruned_exec == 3
