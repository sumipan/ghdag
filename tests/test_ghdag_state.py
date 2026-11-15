"""Tests for ghdag.io.done — §5.3 acceptance criteria."""

from ghdag.io.done import is_done, load_done_from_dir, mark_done


class TestState:
    """§5.3 state.py test cases"""

    def test_mark_done_and_is_done(self, tmp_path):
        """mark_done + is_done: is_done is True after mark_done"""
        mark_done(tmp_path, "uuid-x", 0)
        assert is_done(tmp_path, "uuid-x") is True

    def test_not_done(self, tmp_path):
        """Incomplete: is_done is False (file not created)"""
        assert is_done(tmp_path, "uuid-y") is False

    def test_load_done_nonexistent_dir(self, tmp_path):
        """Missing done directory: returns empty set (no exception)"""
        result = load_done_from_dir(tmp_path / "nonexistent")
        assert result == set()
