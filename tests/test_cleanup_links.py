"""Integration tests for cleanup wiki-link rewrite (Issue #759 AC10–AC13)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ghdag.cleanup.orchestrator import cleanup_queue

FIXED_NOW_TS = 1762600000.0
FIXED_NOW_DT = datetime.fromtimestamp(FIXED_NOW_TS, tz=timezone.utc)

@pytest.fixture(autouse=True)
def _fixed_cleanup_clock(monkeypatch: pytest.MonkeyPatch) -> None:

    class _FixedDatetime(datetime):

        @classmethod
        def now(cls, tz=None):
            return FIXED_NOW_DT if tz is None else FIXED_NOW_DT.astimezone(tz)
    monkeypatch.setattr('ghdag.cleanup.orchestrator.datetime', _FixedDatetime)
TS = '20260101120000'
UUID_A = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
UUID_B = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
UUID_C = 'cccccccc-cccc-cccc-cccc-cccccccccccc'
UUID_D = 'dddddddd-dddd-dddd-dddd-dddddddddddd'

def _set_mtime(path: Path, days_ago: float) -> None:
    import os
    t = FIXED_NOW_TS - days_ago * 86400
    os.utime(path, (t, t))

def _setup_dirs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    queue_dir = tmp_path / 'jobs'
    archive_dir = tmp_path / 'jobs' / 'archive'
    done_dir = tmp_path / 'jobs' / 'done'
    exec_md = queue_dir / 'exec.jsonl'
    queue_dir.mkdir()
    archive_dir.mkdir(parents=True)
    done_dir.mkdir(parents=True)
    return (queue_dir, archive_dir, done_dir, exec_md)

def _make_exec_jsonl(exec_md: Path, entries: list[str]) -> None:
    lines = [json.dumps({'uuid': uuid, 'command': 'cat order.md | claude', 'depends': []}) + '\n' for uuid in entries]
    exec_md.write_text(''.join(lines), encoding='utf-8')

def _make_done_flag(done_dir: Path, uuid: str) -> None:
    done_dir.mkdir(parents=True, exist_ok=True)
    (done_dir / uuid).write_text('0', encoding='utf-8')

def _read_audit_records(audit_path: Path) -> list[dict]:
    if not audit_path.exists():
        return []
    return [json.loads(line) for line in audit_path.read_text(encoding='utf-8').splitlines() if line.strip()]

class TestCleanupLinkRewriteRemaining:

    def test_cleanup_rewrites_remaining_queue_wikilinks(self, tmp_path: Path) -> None:
        queue_dir, archive_dir, done_dir, exec_md = _setup_dirs(tmp_path)
        order_d_name = f'{TS}-claude-order-{UUID_D}.md'
        order_c = queue_dir / f'{TS}-claude-order-{UUID_C}.md'
        order_d = queue_dir / order_d_name
        order_c.write_text(f'wait for [[jobs/{order_d_name}]]\n', encoding='utf-8')
        order_d.write_text('order d', encoding='utf-8')
        _set_mtime(order_d, days_ago=2)
        _make_done_flag(done_dir, UUID_D)
        _make_exec_jsonl(exec_md, [UUID_D])
        cleanup_queue(queue_dir=queue_dir, archive_dir=archive_dir, done_dir=done_dir, exec_jsonl=exec_md, cutoff_days=1)
        assert order_c.exists()
        assert f'[[jobs/archive/2026-01/{order_d_name}]]' in order_c.read_text(encoding='utf-8')
        audit_records = _read_audit_records(queue_dir / 'audit.jsonl')
        assert any((r.get('source') == 'cleanup_link_rewrite' for r in audit_records))
