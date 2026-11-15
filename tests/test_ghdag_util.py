"""Tests for ghdag.dag._util — tee path extraction (AC 5-1 ~ 5-5) and reader functions."""

from ghdag.dag._util import _extract_tee_target


class TestExtractTeeTarget:
    def test_result_path_priority(self):
        """5-1: non-None result_path takes precedence over tee in the command"""
        result = _extract_tee_target("cat foo | tee out.md", result_path="explicit.md")
        assert result == "explicit.md"

    def test_non_md_extension(self):
        """5-2: non-.md extensions can be extracted"""
        result = _extract_tee_target("cat foo | tee result.json")
        assert result == "result.json"

    def test_quoted_path_with_space(self):
        """5-3: quoted paths (with spaces) can be extracted"""
        result = _extract_tee_target('cat foo | tee "path with space.md"')
        assert result == "path with space.md"

    def test_no_tee_no_result_path(self):
        """5-4: no tee and no result_path → None"""
        result = _extract_tee_target("echo hello", result_path=None)
        assert result is None

    def test_md_path_backward_compat(self):
        """5-5: legacy .md path (backward compatible)"""
        result = _extract_tee_target("cat order.md | claude -p '...' | tee result.md", result_path=None)
        assert result == "result.md"

    def test_tee_with_minus_a_flag(self):
        """Can extract path even with tee -a flag"""
        result = _extract_tee_target("cmd | tee -a output.txt")
        assert result == "output.txt"

    def test_result_path_none_falls_back_to_regex(self):
        """Regex fallback when result_path=None"""
        result = _extract_tee_target("cmd | tee out.yaml", result_path=None)
        assert result == "out.yaml"
