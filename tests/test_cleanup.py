"""Tests for ghdag.cleanup — AC1〜AC10 および Issue-856 修正テストを含む。"""

from __future__ import annotations

import time
from datetime import datetime, timezone
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


def _make_exec_jsonl(exec_jsonl: Path, entries: list[str]) -> None:
    import json
    lines = [json.dumps({"uuid": uuid, "command": "cat queue/order.md | claude"}) + "\n" for uuid in entries]
    exec_jsonl.write_text("".join(lines), encoding="utf-8")


def _make_done_flag(done_dir: Path, uuid: str) -> None:
    done_dir.mkdir(parents=True, exist_ok=True)
    (done_dir / uuid).touch()


def _setup_dirs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    queue_dir = tmp_path / "jobs"
    archive_dir = tmp_path / "jobs" / "archive"
    done_dir = tmp_path / "jobs" / "done"
    exec_md = queue_dir / "exec.jsonl"
    queue_dir.mkdir()
    archive_dir.mkdir(parents=True)
    done_dir.mkdir(parents=True)
    return queue_dir, archive_dir, done_dir, exec_md


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
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        order, result = _make_queue_files(queue_dir, UUID_A)
        _set_mtime(order, days_ago=2)
        _make_done_flag(done_dir, UUID_A)
        _make_exec_md(exec_md, [UUID_A])

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
        _make_exec_md(exec_md, [UUID_A])

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
        _make_exec_md(exec_md, [UUID_A])

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


# ---------------------------------------------------------------------------
# AC2: 孤立タスクのアーカイブ
# ---------------------------------------------------------------------------


class TestArchivedOrphan:
    def test_orphan_task_older_than_orphan_days_is_archived(self, tmp_path):
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        order, result = _make_queue_files(queue_dir, UUID_B)
        _set_mtime(order, days_ago=10)
        _make_exec_md(exec_md, [UUID_B])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            orphan_days=7,
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
        _make_exec_md(exec_md, [UUID_B])

        cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            orphan_days=7,
        )

        dest_dir = archive_dir / "2026-01" / "orphan"
        assert (dest_dir / order.name).exists()
        assert (dest_dir / result.name).exists()

    def test_orphan_task_newer_than_orphan_days_is_not_archived(self, tmp_path):
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        order, result = _make_queue_files(queue_dir, UUID_B)
        _set_mtime(order, days_ago=3)
        _make_exec_md(exec_md, [UUID_B])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
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
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        order, result = _make_queue_files(queue_dir, UUID_A)
        _set_mtime(order, days_ago=2)
        _make_done_flag(done_dir, UUID_A)
        _make_exec_md(exec_md, [UUID_A])
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
        _make_exec_md(exec_md, [UUID_A])

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


# ---------------------------------------------------------------------------
# AC4: result のみ存在（order 欠損）の完了済みタスク
# ---------------------------------------------------------------------------


class TestResultOnlyDone:
    def test_result_only_done_task_archived_without_error(self, tmp_path):
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        _, result = _make_queue_files(queue_dir, UUID_C, make_order=False)
        _set_mtime(result, days_ago=2)
        _make_done_flag(done_dir, UUID_C)
        _make_exec_md(exec_md, [UUID_C])

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
# AC5: order のみ存在（result 欠損）の孤立タスク
# ---------------------------------------------------------------------------


class TestOrderOnlyOrphan:
    def test_order_only_orphan_task_archived_without_error(self, tmp_path):
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        order, _ = _make_queue_files(queue_dir, UUID_C, make_result=False)
        _set_mtime(order, days_ago=10)
        _make_exec_md(exec_md, [UUID_C])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
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
        archive_dir = tmp_path / "jobs" / "archive"
        done_dir = tmp_path / "jobs" / "done"
        exec_md = tmp_path / "exec.md"
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
# AC7: マッチするファイルが 0 件
# ---------------------------------------------------------------------------


class TestNoMatchingFiles:
    def test_empty_queue_dir_returns_zero_counts(self, tmp_path):
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        _make_exec_md(exec_md, [])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
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
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        order, _ = _make_queue_files(queue_dir, UUID_A)
        _make_done_flag(done_dir, UUID_A)
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
                archive_dir=archive_dir,
                done_dir=done_dir,
                exec_md=exec_md,
                cutoff_days=1,
            )

        assert res.archived_done == 1


# ---------------------------------------------------------------------------
# AC9: exec.md が存在しない
# ---------------------------------------------------------------------------


class TestExecMdMissing:
    def test_missing_exec_md_skips_pruning_but_archives(self, tmp_path):
        queue_dir, archive_dir, done_dir, _ = _setup_dirs(tmp_path)
        exec_md = queue_dir / "exec.md"  # does not exist
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


# ---------------------------------------------------------------------------
# AC10: UUID の大文字・小文字混在
# ---------------------------------------------------------------------------


class TestUUIDCaseInsensitive:
    def test_uppercase_uuid_in_filename_matches_lowercase_done_flag(self, tmp_path):
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        uuid_upper = UUID_A.upper()
        order, _ = _make_queue_files(queue_dir, uuid_upper)
        _set_mtime(order, days_ago=2)
        _make_done_flag(done_dir, UUID_A.lower())
        _make_exec_md(exec_md, [UUID_A.lower()])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            cutoff_days=1,
        )

        assert res.archived_done == 1


# ---------------------------------------------------------------------------
# AC1 (追加): 複合ツール名（ハイフン入り）のファイルが cleanup 対象になる
# ---------------------------------------------------------------------------


class TestCompoundToolName:
    def test_compound_tool_name_done_task_is_archived(self, tmp_path):
        """claude-investigator のような複合ツール名ファイルが完了済みアーカイブされる"""
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        # 複合ツール名でファイルを作成
        order = queue_dir / f"{TS}-claude-investigator-order-{UUID_A}.md"
        result = queue_dir / f"{TS}-claude-investigator-result-{UUID_A}.md"
        order.write_text("order")
        result.write_text("result")
        _set_mtime(order, days_ago=2)
        _make_done_flag(done_dir, UUID_A)
        _make_exec_md(exec_md, [UUID_A])

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
        """gemini-redelegator のような複合ツール名ファイルが完了済みアーカイブされる"""
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        order = queue_dir / f"{TS}-gemini-redelegator-order-{UUID_B}.md"
        order.write_text("order")
        _set_mtime(order, days_ago=2)
        _make_done_flag(done_dir, UUID_B)
        _make_exec_md(exec_md, [UUID_B])

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
        """cursor-investigator のような複合ツール名の孤立タスクがアーカイブされる"""
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        order = queue_dir / f"{TS}-cursor-investigator-order-{UUID_C}.md"
        order.write_text("order")
        _set_mtime(order, days_ago=10)
        _make_exec_md(exec_md, [UUID_C])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            orphan_days=7,
        )

        assert res.archived_orphan == 1
        assert not order.exists()


# ---------------------------------------------------------------------------
# AC2 (追加): stderr ファイルが cleanup 対象になる
# ---------------------------------------------------------------------------


class TestStderrKind:
    def test_stderr_file_done_task_is_archived(self, tmp_path):
        """claude-stderr の stderr ファイルが完了済みとしてアーカイブされる"""
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        stderr_file = queue_dir / f"{TS}-claude-stderr-{UUID_A}.md"
        stderr_file.write_text("stderr content")
        _set_mtime(stderr_file, days_ago=2)
        _make_done_flag(done_dir, UUID_A)
        _make_exec_md(exec_md, [UUID_A])

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
        """cursor-stderr の孤立タスクがアーカイブされる"""
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        stderr_file = queue_dir / f"{TS}-cursor-stderr-{UUID_B}.md"
        stderr_file.write_text("stderr content")
        _set_mtime(stderr_file, days_ago=10)
        _make_exec_md(exec_md, [UUID_B])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            orphan_days=7,
        )

        assert res.archived_orphan == 1
        assert not stderr_file.exists()

    def test_gemini_stderr_matches_as_valid_kind(self, tmp_path):
        """gemini-stderr ファイルが QUEUE_FILE_RE にマッチする"""
        from ghdag.cleanup import QUEUE_FILE_RE
        fname = f"{TS}-gemini-stderr-{UUID_C}.md"
        assert QUEUE_FILE_RE.match(fname) is not None


# ---------------------------------------------------------------------------
# CleanupResult dataclass
# ---------------------------------------------------------------------------


class TestCleanupResult:
    def test_result_fields(self):
        r = CleanupResult(archived_done=1, archived_orphan=2, pruned_exec=3)
        assert r.archived_done == 1
        assert r.archived_orphan == 2
        assert r.pruned_exec == 3


# ---------------------------------------------------------------------------
# Issue-856: JSONL prune（AC1）
# ---------------------------------------------------------------------------


class TestJsonlPrune:
    def test_jsonl_prune_removes_target_uuid_line(self, tmp_path):
        """JSONL 形式の exec ファイルからアーカイブ対象 UUID 行を除去する"""
        queue_dir, archive_dir, done_dir, exec_jsonl = _setup_dirs(tmp_path)
        order, _ = _make_queue_files(queue_dir, UUID_A)
        _set_mtime(order, days_ago=2)
        _make_done_flag(done_dir, UUID_A)
        _make_exec_jsonl(exec_jsonl, [UUID_A, UUID_B])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_jsonl,
            cutoff_days=1,
        )

        assert res.pruned_exec == 1
        content = exec_jsonl.read_text()
        assert UUID_A not in content
        assert UUID_B in content

    def test_jsonl_prune_keeps_non_matching_uuid(self, tmp_path):
        """別 UUID の JSONL 行は残る"""
        queue_dir, archive_dir, done_dir, exec_jsonl = _setup_dirs(tmp_path)
        order, _ = _make_queue_files(queue_dir, UUID_A)
        _set_mtime(order, days_ago=2)
        _make_done_flag(done_dir, UUID_A)
        _make_exec_jsonl(exec_jsonl, [UUID_B])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_jsonl,
            cutoff_days=1,
        )

        assert res.pruned_exec == 0
        assert UUID_B in exec_jsonl.read_text()

    def test_jsonl_prune_keeps_invalid_json_line(self, tmp_path):
        """パース不能な行は除去しない"""
        import json
        queue_dir, archive_dir, done_dir, exec_jsonl = _setup_dirs(tmp_path)
        order, _ = _make_queue_files(queue_dir, UUID_A)
        _set_mtime(order, days_ago=2)
        _make_done_flag(done_dir, UUID_A)

        invalid_line = "NOT_VALID_JSON\n"
        valid_line = json.dumps({"uuid": UUID_A, "command": "cmd"}) + "\n"
        exec_jsonl.write_text(invalid_line + valid_line, encoding="utf-8")

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_jsonl,
            cutoff_days=1,
        )

        assert res.pruned_exec == 1
        content = exec_jsonl.read_text()
        assert "NOT_VALID_JSON" in content
        assert UUID_A not in content

    def test_jsonl_exec_md_backward_compat(self, tmp_path):
        """exec.md 形式（UUID: command）が JSONL 対応後も動作する（後方互換）"""
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        exec_md_path = queue_dir / "exec.md"
        order, _ = _make_queue_files(queue_dir, UUID_A)
        _set_mtime(order, days_ago=2)
        _make_done_flag(done_dir, UUID_A)
        _make_exec_md(exec_md_path, [UUID_A, UUID_B])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md_path,
            cutoff_days=1,
        )

        assert res.pruned_exec == 1
        content = exec_md_path.read_text()
        assert UUID_A not in content
        assert UUID_B in content


# ---------------------------------------------------------------------------
# Issue-856: orphan done マーカー付与（AC2）
# ---------------------------------------------------------------------------


class TestOrphanDoneMark:
    def test_orphan_archive_creates_done_marker(self, tmp_path):
        """orphan アーカイブ前に ORPHAN_ARCHIVED の done マーカーを作成する"""
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
        )

        assert res.archived_orphan == 1
        done_flag = done_dir / UUID_B
        assert done_flag.exists()
        assert "ORPHAN_ARCHIVED" in done_flag.read_text()

    def test_orphan_done_marker_created_before_file_move(self, tmp_path):
        """done マーカーはファイル移動の前に作成される（DagEngine が誤認しないよう）"""
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
            with patch("builtins.open", wraps=open) as mock_open:
                cleanup_queue(
                    queue_dir=queue_dir,
                    archive_dir=archive_dir,
                    done_dir=done_dir,
                    exec_md=exec_jsonl,
                    orphan_days=7,
                )

        done_flag = done_dir / UUID_B
        assert done_flag.exists()


# ---------------------------------------------------------------------------
# Issue-856: done マーカー削除順序（AC3）
# ---------------------------------------------------------------------------


class TestDoneDeleteOrder:
    def test_exec_pruned_before_done_marker_deleted(self, tmp_path):
        """exec prune が完了した後に done マーカーを削除する"""
        queue_dir, archive_dir, done_dir, exec_jsonl = _setup_dirs(tmp_path)
        order, _ = _make_queue_files(queue_dir, UUID_A)
        _set_mtime(order, days_ago=2)
        _make_done_flag(done_dir, UUID_A)
        _make_exec_jsonl(exec_jsonl, [UUID_A])

        call_order: list[str] = []

        original_unlink = Path.unlink
        original_write_text = Path.write_text

        def tracking_unlink(self, missing_ok=False):
            call_order.append(f"unlink:{self.name}")
            return original_unlink(self, missing_ok=missing_ok)

        def tracking_write_text(self, data, *args, **kwargs):
            if self.name == exec_jsonl.name:
                call_order.append(f"write_exec:{self.name}")
            return original_write_text(self, data, *args, **kwargs)

        with patch.object(Path, "unlink", tracking_unlink), \
             patch.object(Path, "write_text", tracking_write_text):
            cleanup_queue(
                queue_dir=queue_dir,
                archive_dir=archive_dir,
                done_dir=done_dir,
                exec_md=exec_jsonl,
                cutoff_days=1,
            )

        exec_write_idx = next(
            (i for i, x in enumerate(call_order) if x.startswith("write_exec:")), None
        )
        done_unlink_idx = next(
            (i for i, x in enumerate(call_order) if x == f"unlink:{UUID_A}"), None
        )
        assert exec_write_idx is not None, f"exec write not found: {call_order}"
        assert done_unlink_idx is not None, f"done unlink not found: {call_order}"
        assert exec_write_idx < done_unlink_idx, (
            f"exec prune should happen before done marker deletion, "
            f"but order was: {call_order}"
        )
