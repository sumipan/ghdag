"""Tests for ghdag.dag._util — tee path extraction (AC 5-1 ~ 5-5)."""

from ghdag.dag._util import _extract_tee_target


class TestExtractTeeTarget:
    def test_result_path_priority(self):
        """5-1: result_path が非 None ならコマンドの tee より優先"""
        result = _extract_tee_target("cat foo | tee out.md", result_path="explicit.md")
        assert result == "explicit.md"

    def test_non_md_extension(self):
        """5-2: .md 以外の拡張子も抽出できる"""
        result = _extract_tee_target("cat foo | tee result.json")
        assert result == "result.json"

    def test_quoted_path_with_space(self):
        """5-3: クォート付きパス（スペースあり）を抽出できる"""
        result = _extract_tee_target('cat foo | tee "path with space.md"')
        assert result == "path with space.md"

    def test_no_tee_no_result_path(self):
        """5-4: tee なし・result_path なし → None"""
        result = _extract_tee_target("echo hello", result_path=None)
        assert result is None

    def test_md_path_backward_compat(self):
        """5-5: 従来の .md パス（後方互換）"""
        result = _extract_tee_target("cat order.md | claude -p '...' | tee result.md", result_path=None)
        assert result == "result.md"

    def test_tee_with_minus_a_flag(self):
        """tee -a フラグ付きでもパスを抽出できる"""
        result = _extract_tee_target("cmd | tee -a output.txt")
        assert result == "output.txt"

    def test_result_path_none_falls_back_to_regex(self):
        """result_path=None のとき正規表現フォールバック"""
        result = _extract_tee_target("cmd | tee out.yaml", result_path=None)
        assert result == "out.yaml"
