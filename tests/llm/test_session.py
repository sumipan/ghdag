from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from ghdag.llm.session import SessionStore


def test_record_and_lookup_roundtrip(tmp_path):
    store = SessionStore(tmp_path / ".sessions")

    store.record("k1", "claude", "sess-abc")
    resolved = store.lookup("k1")

    assert resolved is not None
    assert resolved.engine == "claude"
    assert resolved.session_id == "sess-abc"
    assert resolved.created_at.tzinfo is not None
    assert resolved.created_at.utcoffset() == timedelta(0)


def test_lookup_returns_none_when_missing(tmp_path):
    store = SessionStore(tmp_path / ".sessions")

    assert store.lookup("missing") is None


def test_lookup_respects_max_age(tmp_path):
    store = SessionStore(tmp_path / ".sessions")
    store.record("k1", "claude", "sess-abc")

    assert store.lookup("k1", max_age=timedelta(seconds=0)) is None


def test_invalidate_removes_record(tmp_path):
    store = SessionStore(tmp_path / ".sessions")
    store.record("k1", "claude", "sess-abc")

    assert store.invalidate("k1") is True
    assert store.lookup("k1") is None
    assert store.invalidate("k1") is False


def test_gc_deletes_only_expired_sessions(tmp_path):
    store = SessionStore(tmp_path / ".sessions")
    store.record("new", "claude", "sess-new")

    store_dir = tmp_path / ".sessions"
    old_path = store_dir / "old.json"
    old_data = {
        "engine": "claude",
        "session_id": "sess-old",
        "created_at": (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
    }
    old_path.write_text(json.dumps(old_data), encoding="utf-8")

    deleted = store.gc(max_age=timedelta(days=7))

    assert deleted == 1
    assert store.lookup("old") is None
    assert store.lookup("new") is not None


def test_lookup_legacy_record_falls_back_to_mtime(tmp_path):
    store_dir = tmp_path / ".sessions"
    store_dir.mkdir(parents=True, exist_ok=True)
    legacy_path = store_dir / "legacy.json"
    legacy_path.write_text(
        json.dumps({"engine": "claude", "session_id": "sess-legacy"}),
        encoding="utf-8",
    )
    expected = datetime.now(timezone.utc) - timedelta(hours=3)
    os.utime(legacy_path, (expected.timestamp(), expected.timestamp()))

    store = SessionStore(store_dir)
    resolved = store.lookup("legacy")

    assert resolved is not None
    assert resolved.engine == "claude"
    assert resolved.session_id == "sess-legacy"
    assert abs((resolved.created_at - expected).total_seconds()) < 2
