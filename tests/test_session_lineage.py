"""SessionRecord lineage / SessionStore.record_compacted tests (Issue #90)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from ghdag.llm.session import SessionStore

_FIXED_CREATED_AT = "2026-06-01T12:00:00+00:00"


def test_record_compacted_stores_lineage_fields(tmp_path):
    store = SessionStore(tmp_path / ".sessions")
    store.record("parent", "claude", "sess-parent")

    store.record_compacted(
        "compacted",
        "claude",
        "sess-compacted",
        parent_key="parent",
        summary_tokens=420,
    )

    resolved = store.lookup("compacted")
    assert resolved is not None
    assert resolved.session_id == "sess-compacted"
    assert resolved.session_id != "sess-parent"
    assert resolved.parent_session_id == "sess-parent"
    assert resolved.is_compacted is True
    assert resolved.summary_tokens == 420


def test_lookup_legacy_record_defaults_lineage_fields(tmp_path):
    store = SessionStore(tmp_path / ".sessions")
    store.record("legacy", "claude", "sess-legacy")

    resolved = store.lookup("legacy")
    assert resolved is not None
    assert resolved.parent_session_id is None
    assert resolved.is_compacted is False
    assert resolved.summary_tokens is None


def test_lookup_reads_lineage_from_json_without_private_apis(tmp_path):
    store_dir = tmp_path / ".sessions"
    store_dir.mkdir(parents=True)
    payload = {
        "engine": "claude",
        "session_id": "sess-c",
        "created_at": _FIXED_CREATED_AT,
        "parent_session_id": "sess-p",
        "is_compacted": True,
        "summary_tokens": 12,
    }
    (store_dir / "k.json").write_text(json.dumps(payload), encoding="utf-8")

    store = SessionStore(store_dir)
    resolved = store.lookup("k")

    assert resolved.engine == "claude"
    assert resolved.session_id == "sess-c"
    assert resolved.created_at == datetime.fromisoformat(_FIXED_CREATED_AT)
    assert resolved.parent_session_id == "sess-p"
    assert resolved.is_compacted is True
    assert resolved.summary_tokens == 12


def test_gc_preserves_unexpired_compacted_lineage(tmp_path):
    store = SessionStore(tmp_path / ".sessions")
    store.record("parent", "claude", "sess-parent")
    store.record_compacted(
        "compacted",
        "claude",
        "sess-compacted",
        parent_key="parent",
        summary_tokens=10,
    )

    deleted = store.gc(max_age=timedelta(days=7))

    assert deleted == 0
    compacted = store.lookup("compacted")
    assert compacted is not None
    assert compacted.parent_session_id == "sess-parent"
    assert compacted.is_compacted is True


def test_record_signature_unchanged(tmp_path):
    store = SessionStore(tmp_path / ".sessions")
    store.record("k", "claude", "sess-1")
    assert store.lookup("k") is not None
