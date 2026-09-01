from __future__ import annotations

from ghdag.io import sessions


def test_save_load_roundtrip(tmp_path):
    sessions.save(tmp_path, "task-1", "claude", "sess_abc")
    loaded = sessions.load(tmp_path, "task-1")
    assert loaded == ("claude", "sess_abc")


def test_load_returns_none_when_file_missing(tmp_path):
    assert sessions.load(tmp_path, "missing-task") is None
