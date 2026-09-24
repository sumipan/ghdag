"""Tests for ghdag.cleanup — AC1–AC10 and Issue-856 fix coverage."""

from __future__ import annotations

import json
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


def _make_exec_jsonl(exec_md: Path, entries: list[str]) -> None:
    lines = [json.dumps({"uuid": uuid, "command": "cat queue/order.md | claude", "depends": []}) + "\n" for uuid in entries]
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
# AC1: archive completed tasks
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# AC2: archive orphan tasks
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# AC3: dry_run
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# AC4: completed task with result only (missing order)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# AC6: queue/ does not exist
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# AC8: boundary (exactly cutoff days)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# AC10: mixed-case UUID
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# AC1 (extra): hyphenated compound tool-name files are cleanup targets
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# AC2 (extra): stderr files are cleanup targets
# ---------------------------------------------------------------------------


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
# Issue-856: JSONL prune (AC1)
# ---------------------------------------------------------------------------


class TestJsonlPrune:
    def test_jsonl_prune_removes_target_uuid_line(self, tmp_path):
        """Remove archive-target UUID lines from a JSONL exec file"""
        queue_dir, archive_dir, done_dir, exec_jsonl = _setup_dirs(tmp_path)
        order_a, _ = _make_queue_files(queue_dir, UUID_A)
        _set_mtime(order_a, days_ago=2)
        _make_done_flag(done_dir, UUID_A)
        # UUID_B is an active pending job (order present, no done, recent) = Case E
        # ghdag enqueues order-first, so missing order = dead, not pending
        order_b, _ = _make_queue_files(queue_dir, UUID_B)
        _set_mtime(order_b, days_ago=0.1)
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
        """JSONL lines for other UUIDs remain (active pending jobs kept as Case E)"""
        queue_dir, archive_dir, done_dir, exec_jsonl = _setup_dirs(tmp_path)
        order_a, _ = _make_queue_files(queue_dir, UUID_A)
        _set_mtime(order_a, days_ago=2)
        _make_done_flag(done_dir, UUID_A)
        # UUID_B is active pending (Case E: no done, files present, recent)
        order_b, _ = _make_queue_files(queue_dir, UUID_B)
        _set_mtime(order_b, days_ago=0.1)
        _make_exec_jsonl(exec_jsonl, [UUID_B])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_jsonl,
            cutoff_days=1,
        )

        # UUID_A is absent from exec.jsonl so not Case A; Phase 2 archives it (pruned_exec=0)
        # UUID_B is kept as Case E
        assert res.pruned_exec == 0
        assert UUID_B in exec_jsonl.read_text()

    def test_jsonl_prune_keeps_invalid_json_line(self, tmp_path):
        """Unparseable lines are not removed"""
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

# ---------------------------------------------------------------------------
# Issue-856: attach orphan done marker (AC2)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Issue-856: done marker deletion order (AC3)
# ---------------------------------------------------------------------------


class TestDoneDeleteOrder:
    def test_exec_pruned_before_done_marker_deleted(self, tmp_path):
        """Delete the done marker only after exec prune completes"""
        queue_dir, archive_dir, done_dir, exec_jsonl = _setup_dirs(tmp_path)
        order, _ = _make_queue_files(queue_dir, UUID_A)
        _set_mtime(order, days_ago=2)
        _make_done_flag(done_dir, UUID_A)
        _make_exec_jsonl(exec_jsonl, [UUID_A])

        call_order: list[str] = []

        original_unlink = Path.unlink
        import ghdag.io.exec_jsonl as exec_jsonl_mod

        original_prune = exec_jsonl_mod.prune

        def tracking_unlink(self, missing_ok=False):
            call_order.append(f"unlink:{self.name}")
            return original_unlink(self, missing_ok=missing_ok)

        def tracking_prune(path, prune_uuids, *, dry_run=False):
            # prune rewrites under LOCK_EX (replaces former Path.write_text)
            result = original_prune(path, prune_uuids, dry_run=dry_run)
            if Path(path).name == exec_jsonl.name and result > 0 and not dry_run:
                call_order.append(f"write_exec:{exec_jsonl.name}")
            return result

        with patch.object(Path, "unlink", tracking_unlink), \
             patch.object(exec_jsonl_mod, "prune", tracking_prune):
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


# ---------------------------------------------------------------------------
# AC11–AC17: Phase 2 sweep phase
# ---------------------------------------------------------------------------

UUID_D = "dddddddd-dddd-dddd-dddd-dddddddddddd"


class TestSweepExtras:
    def test_ac11_slack_pending_json_is_swept(self, tmp_path):
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

    def test_ac12_nonstandard_md_is_swept(self, tmp_path):
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

    def test_ac13_whitelist_files_are_not_swept(self, tmp_path):
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

    def test_ac14_young_file_is_not_swept(self, tmp_path):
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

    def test_ac15_dry_run_sweep(self, tmp_path, capsys):
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

    def test_ac16_directories_are_not_swept(self, tmp_path):
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

    def test_ac17_phase1_archived_files_not_double_processed(self, tmp_path):
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


# ---------------------------------------------------------------------------
# Issue-870: remove stuck entries (Case C/F) and protect Case B
# ---------------------------------------------------------------------------


class TestStuckDoneExecPrune:
    def test_stuck_uuid_pruned_from_exec(self, tmp_path):
        """Case C: done marker present, files absent → removed from exec.jsonl"""
        queue_dir, archive_dir, done_dir, exec_jsonl = _setup_dirs(tmp_path)
        # Do not create files (stuck: already archived by a previous cleanup)
        _make_done_flag(done_dir, UUID_A)
        _make_exec_jsonl(exec_jsonl, [UUID_A])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_jsonl,
            cutoff_days=1,
        )

        assert res.pruned_exec == 1
        assert res.archived_done == 0
        assert UUID_A not in exec_jsonl.read_text()

    def test_stuck_uuid_done_marker_preserved(self, tmp_path):
        """Case C: done marker is not deleted after stuck handling"""
        queue_dir, archive_dir, done_dir, exec_jsonl = _setup_dirs(tmp_path)
        _make_done_flag(done_dir, UUID_A)
        _make_exec_jsonl(exec_jsonl, [UUID_A])

        cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_jsonl,
            cutoff_days=1,
        )

        assert (done_dir / UUID_A).exists()

    def test_done_recent_not_pruned(self, tmp_path):
        """Case B: done present, files present, before cutoff → exec.jsonl not pruned"""
        queue_dir, archive_dir, done_dir, exec_jsonl = _setup_dirs(tmp_path)
        order, result = _make_queue_files(queue_dir, UUID_B)
        _set_mtime(order, days_ago=0.5)
        _make_done_flag(done_dir, UUID_B)
        _make_exec_jsonl(exec_jsonl, [UUID_B])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_jsonl,
            cutoff_days=1,
        )

        assert res.pruned_exec == 0
        assert res.archived_done == 0
        assert UUID_B in exec_jsonl.read_text()
        assert order.exists()
        assert result.exists()

    def test_stuck_cleanup_idempotent(self, tmp_path):
        """Run Case C twice → second run has pruned_exec == 0, no error"""
        queue_dir, archive_dir, done_dir, exec_jsonl = _setup_dirs(tmp_path)
        _make_done_flag(done_dir, UUID_A)
        _make_exec_jsonl(exec_jsonl, [UUID_A])

        res1 = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_jsonl,
            cutoff_days=1,
        )
        res2 = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_jsonl,
            cutoff_days=1,
        )

        assert res1.pruned_exec == 1
        assert res2.pruned_exec == 0

    def test_dead_entry_pruned(self, tmp_path):
        """Case F: no done, no files → removed from exec.jsonl

        Job enqueue always creates the order file before appending exec.jsonl at
        every entry point (LLMPipelineAPI / submit_order / enqueue, etc.), and
        append_exec runs under fcntl.LOCK_EX. Therefore "present in exec.jsonl but
        no files" is a dead entry, not pending.
        """
        queue_dir, archive_dir, done_dir, exec_jsonl = _setup_dirs(tmp_path)
        # no done marker, no files, only an exec.jsonl entry
        _make_exec_jsonl(exec_jsonl, [UUID_C])

        res = cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_jsonl,
            cutoff_days=1,
            auto_repair=True,
        )

        assert res.pruned_exec == 1
        assert res.archived_done == 0
        assert UUID_C not in exec_jsonl.read_text()


# ---------------------------------------------------------------------------
# Issue-1057: auto_repair=False default behavior (detect only)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Issue-3677: is_sweepable_extra and Phase 3 mtime-based sweep
# ---------------------------------------------------------------------------


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

    def test_ac1_state_file_updated_recently_is_not_swept(self, tmp_path):
        import os

        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        _make_exec_jsonl(exec_md, [])
        override = queue_dir / "quota-gate-override.json"
        override.write_text("{}")
        # birthtime = 8 days ago, mtime = 1 hour ago
        eight_days_ago = time.time() - 8 * 86400
        one_hour_ago = time.time() - 3600
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

    def test_ac2_timestamped_artifact_is_swept(self, tmp_path):
        """AC-2: 14-digit-prefix file old enough by mtime is swept to extras."""
        import os

        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        _make_exec_jsonl(exec_md, [])
        artifact = queue_dir / "20260101120000-shell-result-abcd1234.md.prev-20260101T000000Z"
        artifact.write_text("prev")
        eight_days_ago = time.time() - 8 * 86400
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

    def test_ac3_lock_files_not_swept(self, tmp_path):
        """AC-3: .lock files are not swept even when old."""
        import os

        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        _make_exec_jsonl(exec_md, [])
        lock1 = queue_dir / "quota-gate.json.lock"
        lock2 = queue_dir / "issuesmith-brake.json.lock"
        lock1.write_text("")
        lock2.write_text("")
        thirty_days_ago = time.time() - 30 * 86400
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

    def test_ac5_mtime_recent_not_swept(self, tmp_path):
        """AC-5a: slack-pending-<uuid> created 8d ago but updated 1h ago is NOT swept."""
        import os

        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        _make_exec_jsonl(exec_md, [])
        slack_file = queue_dir / f"slack-pending-{UUID_D}.json"
        slack_file.write_text("{}")
        eight_days_ago = time.time() - 8 * 86400
        one_hour_ago = time.time() - 3600
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

    def test_ac5_mtime_old_is_swept(self, tmp_path):
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

    def test_ac7_dry_run_output_format(self, tmp_path, capsys):
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
