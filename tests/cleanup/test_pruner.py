"""Tests for ghdag.cleanup — AC1–AC10 and Issue-856 fix coverage."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
from ghdag_cleanup_conftest_helpers import (
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


class TestDoneDeleteOrder:
    def test_done_marker_unlinked_after_exec_prune_writes(self, tmp_path):
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
        every entry point (LLMPipelineAPI / enqueue, etc.), and
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
