"""Tests for ghdag.dag.state — §5.3 acceptance criteria."""

import concurrent.futures

from ghdag.dag.state import is_done, load_done_from_dir, load_succeeded_from_dir, mark_done


class TestState:
    """§5.3 state.py test cases"""

    def test_mark_done_and_is_done(self, tmp_path):
        """mark_done + is_done: is_done is True after mark_done"""
        mark_done(tmp_path, "uuid-x", 0)
        assert is_done(tmp_path, "uuid-x") is True

    def test_not_done(self, tmp_path):
        """Incomplete: is_done is False (file not created)"""
        assert is_done(tmp_path, "uuid-y") is False

    def test_load_succeeded_only_zero(self, tmp_path):
        """load_succeeded_from_dir: only status=\"0\" is in the succeeded set"""
        mark_done(tmp_path, "uuid-ok", 0)
        mark_done(tmp_path, "uuid-rejected", "REJECTED")
        mark_done(tmp_path, "uuid-fail", 1)
        mark_done(tmp_path, "uuid-error", "ERROR")

        succeeded = load_succeeded_from_dir(tmp_path)
        assert "uuid-ok" in succeeded
        assert "uuid-rejected" not in succeeded
        assert "uuid-fail" not in succeeded
        assert "uuid-error" not in succeeded

    def test_empty_file_is_success(self, tmp_path):
        """Backward compat: empty file (status=\"\") is treated as success"""
        (tmp_path / "uuid-empty").write_text("")
        succeeded = load_succeeded_from_dir(tmp_path)
        assert "uuid-empty" in succeeded

    def test_load_done_nonexistent_dir(self, tmp_path):
        """Missing done directory: returns empty set (no exception)"""
        result = load_done_from_dir(tmp_path / "nonexistent")
        assert result == set()

    def test_load_done_includes_all(self, tmp_path):
        """load_done_from_dir returns all UUIDs regardless of success/failure"""
        mark_done(tmp_path, "uuid-ok", 0)
        mark_done(tmp_path, "uuid-fail", 1)
        mark_done(tmp_path, "uuid-rejected", "REJECTED")

        done = load_done_from_dir(tmp_path)
        assert done == {"uuid-ok", "uuid-fail", "uuid-rejected"}

    def test_mark_done_concurrent_no_empty_file(self, tmp_path):
        """TC1: parallel mark_done (10 threads × 100) does not empty files"""
        def write_repeatedly(i: int) -> None:
            for _ in range(100):
                mark_done(tmp_path, "test-uuid", i)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(write_repeatedly, i) for i in range(10)]
            for f in futures:
                f.result()

        content = (tmp_path / "test-uuid").read_text()
        assert len(content) > 0
        assert int(content) in range(10)
