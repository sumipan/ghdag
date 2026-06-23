"""Integration tests for cleanup wiki-link rewrite (Issue #759 AC10–AC13)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

from ghdag.cleanup import cleanup_queue

TS = "20260101120000"
UUID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
UUID_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
UUID_C = "cccccccc-cccc-cccc-cccc-cccccccccccc"
UUID_D = "dddddddd-dddd-dddd-dddd-dddddddddddd"


def _set_mtime(path: Path, days_ago: float) -> None:
    import os

    t = time.time() - days_ago * 86400
    os.utime(path, (t, t))


def _setup_dirs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    queue_dir = tmp_path / "jobs"
    archive_dir = tmp_path / "jobs" / "archive"
    done_dir = tmp_path / "jobs" / "done"
    exec_md = queue_dir / "exec.jsonl"
    queue_dir.mkdir()
    archive_dir.mkdir(parents=True)
    done_dir.mkdir(parents=True)
    return queue_dir, archive_dir, done_dir, exec_md


def _make_exec_jsonl(exec_md: Path, entries: list[str]) -> None:
    lines = [
        json.dumps({"uuid": uuid, "command": "cat order.md | claude", "depends": []}) + "\n"
        for uuid in entries
    ]
    exec_md.write_text("".join(lines), encoding="utf-8")


def _make_done_flag(done_dir: Path, uuid: str) -> None:
    done_dir.mkdir(parents=True, exist_ok=True)
    (done_dir / uuid).write_text("0", encoding="utf-8")


def _read_audit_records(audit_path: Path) -> list[dict]:
    if not audit_path.exists():
        return []
    return [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]


class TestCleanupLinkRewriteArchived:
    def test_ac10_archived_file_links_and_frontmatter(self, tmp_path: Path) -> None:
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        order_b_name = f"{TS}-claude-order-{UUID_B}.md"
        order_a = queue_dir / f"{TS}-claude-order-{UUID_A}.md"
        order_b = queue_dir / order_b_name
        order_a.write_text(
            "---\nkey: value\n---\n"
            f"upstream: [[jobs/{order_b_name}]]\n",
            encoding="utf-8",
        )
        order_b.write_text("order b", encoding="utf-8")
        _set_mtime(order_a, days_ago=2)
        _set_mtime(order_b, days_ago=2)
        _make_done_flag(done_dir, UUID_A)
        _make_done_flag(done_dir, UUID_B)
        _make_exec_jsonl(exec_md, [UUID_A, UUID_B])

        cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            cutoff_days=1,
        )

        archived_a = archive_dir / "2026-01" / order_a.name
        archived_b = archive_dir / "2026-01" / order_b.name
        assert archived_a.exists()
        assert archived_b.exists()
        content_a = archived_a.read_text(encoding="utf-8")
        assert content_a.startswith("---\nkey: value\n---\n")
        assert f"[[jobs/archive/2026-01/{order_b_name}]]" in content_a

        audit_records = _read_audit_records(archive_dir / "2026-01" / "audit.jsonl")
        rewrite_records = [r for r in audit_records if r.get("source") == "cleanup_link_rewrite"]
        assert len(rewrite_records) >= 1


class TestCleanupLinkRewriteRemaining:
    def test_ac11_remaining_file_links_updated(self, tmp_path: Path) -> None:
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        order_d_name = f"{TS}-claude-order-{UUID_D}.md"
        order_c = queue_dir / f"{TS}-claude-order-{UUID_C}.md"
        order_d = queue_dir / order_d_name
        order_c.write_text(f"wait for [[jobs/{order_d_name}]]\n", encoding="utf-8")
        order_d.write_text("order d", encoding="utf-8")
        _set_mtime(order_d, days_ago=2)
        _make_done_flag(done_dir, UUID_D)
        _make_exec_jsonl(exec_md, [UUID_D])

        cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            cutoff_days=1,
        )

        assert order_c.exists()
        assert f"[[jobs/archive/2026-01/{order_d_name}]]" in order_c.read_text(encoding="utf-8")

        audit_records = _read_audit_records(queue_dir / "audit.jsonl")
        assert any(r.get("source") == "cleanup_link_rewrite" for r in audit_records)


class TestCleanupLinkRewriteDryRun:
    def test_ac12_dry_run_does_not_rewrite_links(self, tmp_path: Path) -> None:
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        order_b_name = f"{TS}-claude-order-{UUID_B}.md"
        order_a = queue_dir / f"{TS}-claude-order-{UUID_A}.md"
        order_b = queue_dir / order_b_name
        original_a = f"link: [[jobs/{order_b_name}]]\n"
        order_a.write_text(original_a, encoding="utf-8")
        order_b.write_text("order b", encoding="utf-8")
        _set_mtime(order_a, days_ago=2)
        _set_mtime(order_b, days_ago=2)
        _make_done_flag(done_dir, UUID_A)
        _make_done_flag(done_dir, UUID_B)
        _make_exec_jsonl(exec_md, [UUID_A, UUID_B])

        cleanup_queue(
            queue_dir=queue_dir,
            archive_dir=archive_dir,
            done_dir=done_dir,
            exec_md=exec_md,
            cutoff_days=1,
            dry_run=True,
        )

        assert order_a.read_text(encoding="utf-8") == original_a
        assert not (archive_dir / "2026-01" / "audit.jsonl").exists()
        assert not (queue_dir / "audit.jsonl").exists()


class TestCleanupLinkRewriteSkipUnchanged:
    def test_ac13_no_md_write_when_no_wikilinks(self, tmp_path: Path) -> None:
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        order_b_name = f"{TS}-claude-order-{UUID_B}.md"
        order_a = queue_dir / f"{TS}-claude-order-{UUID_A}.md"
        order_b = queue_dir / order_b_name
        order_a.write_text("no links here\n", encoding="utf-8")
        order_b.write_text(f"has [[jobs/{order_a.name}]]\n", encoding="utf-8")
        _set_mtime(order_a, days_ago=2)
        _set_mtime(order_b, days_ago=2)
        _make_done_flag(done_dir, UUID_A)
        _make_done_flag(done_dir, UUID_B)
        _make_exec_jsonl(exec_md, [UUID_A, UUID_B])

        with patch("ghdag.cleanup.link_rewriter.md_write") as mock_md_write:
            cleanup_queue(
                queue_dir=queue_dir,
                archive_dir=archive_dir,
                done_dir=done_dir,
                exec_md=exec_md,
                cutoff_days=1,
            )

        rewritten_paths = {call.args[0] for call in mock_md_write.call_args_list}
        assert f"jobs/archive/2026-01/{order_b.name}" in rewritten_paths
        assert f"jobs/archive/2026-01/{order_a.name}" not in rewritten_paths
