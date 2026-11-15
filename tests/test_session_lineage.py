"""SessionRecord lineage / SessionStore.record_compacted tests (Issue #90)."""

from __future__ import annotations

from ghdag.llm.session import SessionStore


def test_record_signature_unchanged(tmp_path):
    store = SessionStore(tmp_path / ".sessions")
    store.record("k", "claude", "sess-1")
    assert store.lookup("k") is not None
