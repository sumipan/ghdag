"""Tests for ghdag.cleanup — AC1–AC10 and Issue-856 fix coverage."""

from __future__ import annotations

import json

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
from ghdag_cleanup_conftest_helpers import (
    UUID_A,
    _make_done_flag,
    _make_exec_jsonl,
    _make_queue_files,
    _set_mtime,
    _setup_dirs,
)

from ghdag.cleanup.orchestrator import cleanup_queue


class TestJsonlPrune:
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
            exec_jsonl=exec_jsonl,
            cutoff_days=1,
        )

        assert res.pruned_exec == 1
        content = exec_jsonl.read_text()
        assert "NOT_VALID_JSON" in content
        assert UUID_A not in content


class TestStuckDoneExecPrune:
    def test_stuck_uuid_done_marker_preserved(self, tmp_path):
        """Case C: done marker is not deleted after stuck handling"""
        queue_dir, archive_dir, done_dir, exec_jsonl = _setup_dirs(tmp_path)
        _make_done_flag(done_dir, UUID_A)
        _make_exec_jsonl(exec_jsonl, [UUID_A])

        cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_jsonl=exec_jsonl,
            cutoff_days=1,
        )

        assert (done_dir / UUID_A).exists()
