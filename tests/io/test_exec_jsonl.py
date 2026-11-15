"""Tests for ghdag.io.exec_jsonl — exec.jsonl I/O consolidation (nexus Issue #2674)."""

from __future__ import annotations

import json
from pathlib import Path


class TestCheckIdempotency:
    def test_missing_key_is_unprocessed(self, tmp_path: Path) -> None:
        from ghdag.io.exec_jsonl import check_idempotency

        path = tmp_path / "exec.jsonl"
        path.write_text(
            json.dumps({"uuid": "u", "command": "x", "idempotency_key": "k1"}) + "\n",
            encoding="utf-8",
        )
        assert check_idempotency(path, "k2") is True
        assert check_idempotency(path, "k1") is False

    def test_missing_file_is_unprocessed(self, tmp_path: Path) -> None:
        from ghdag.io.exec_jsonl import check_idempotency

        assert check_idempotency(tmp_path / "missing.jsonl", "any") is True


def test_parse_idempotency_key_roundtrips_build():
    from ghdag.io.exec_jsonl import build_idempotency_key, parse_idempotency_key

    assert parse_idempotency_key("wf:h:3:2") == ("wf", "h", 3, 2)
    assert parse_idempotency_key(build_idempotency_key("wf", "h", 3)) == ("wf", "h", 3, 0)
    assert parse_idempotency_key(build_idempotency_key("wf", "h", 3, 2)) == ("wf", "h", 3, 2)


def test_parse_idempotency_key_rejects_malformed():
    from ghdag.io.exec_jsonl import parse_idempotency_key

    for bad in ("garbage", "wf:h:x", "wf::3", "wf:h:3:2:9"):
        assert parse_idempotency_key(bad) is None


