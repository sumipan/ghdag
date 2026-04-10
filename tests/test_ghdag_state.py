"""Tests for ghdag.dag.state — §5.3 acceptance criteria."""

from ghdag.dag.state import is_done, load_done_from_dir, load_succeeded_from_dir, mark_done


class TestState:
    """§5.3 state.py テストケース"""

    def test_mark_done_and_is_done(self, tmp_path):
        """mark_done + is_done: mark_done 後に is_done が True"""
        mark_done(tmp_path, "uuid-x", 0)
        assert is_done(tmp_path, "uuid-x") is True

    def test_not_done(self, tmp_path):
        """未完了: is_done が False（ファイル未作成）"""
        assert is_done(tmp_path, "uuid-y") is False

    def test_load_succeeded_only_zero(self, tmp_path):
        """load_succeeded_from_dir: status="0" のみが成功集合に含まれる"""
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
        """後方互換: 空ファイル（status=""）も成功として扱われること"""
        (tmp_path / "uuid-empty").write_text("")
        succeeded = load_succeeded_from_dir(tmp_path)
        assert "uuid-empty" in succeeded

    def test_load_done_nonexistent_dir(self, tmp_path):
        """exec-done ディレクトリ不存在: 空集合を返すこと（例外なし）"""
        result = load_done_from_dir(tmp_path / "nonexistent")
        assert result == set()

    def test_load_done_includes_all(self, tmp_path):
        """load_done_from_dir は成功・失敗問わず全 UUID を返す"""
        mark_done(tmp_path, "uuid-ok", 0)
        mark_done(tmp_path, "uuid-fail", 1)
        mark_done(tmp_path, "uuid-rejected", "REJECTED")

        done = load_done_from_dir(tmp_path)
        assert done == {"uuid-ok", "uuid-fail", "uuid-rejected"}
