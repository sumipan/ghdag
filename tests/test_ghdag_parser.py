"""Tests for ghdag.dag.parser — §5.2 acceptance criteria."""

from pathlib import Path

import pytest

from ghdag.dag.parser import parse_exec_md


@pytest.fixture
def tmp_exec_md(tmp_path):
    """Helper to create a temporary exec.md with given content."""
    def _write(content: str) -> Path:
        p = tmp_path / "exec.md"
        p.write_text(content, encoding="utf-8")
        return p
    return _write


class TestParseExecMd:
    """§5.2 parser.py テストケース"""

    def test_normal_three_tasks(self, tmp_exec_md):
        """正常系: 3 つの Task をパースする"""
        content = (
            "uuid-a: cat order-a.md | claude -p \"...\" | tee result-a.md\n"
            "uuid-b[depends:uuid-a]: echo \"hello\" | tee result-b.md\n"
            "uuid-c[depends:uuid-a,uuid-b][retry:2]: some-command\n"
        )
        tasks = parse_exec_md(tmp_exec_md(content))

        assert len(tasks) == 3

        a = tasks[0]
        assert a.uuid == "uuid-a"
        assert a.depends == []
        assert a.retry == 0
        assert "cat order-a.md" in a.command

        b = tasks[1]
        assert b.uuid == "uuid-b"
        assert b.depends == ["uuid-a"]
        assert b.retry == 0

        c = tasks[2]
        assert c.uuid == "uuid-c"
        assert set(c.depends) == {"uuid-a", "uuid-b"}
        assert c.retry == 2

    def test_blank_and_comment_lines_skipped(self, tmp_exec_md):
        """空行・コメント: # comment 行と空行がスキップされること"""
        content = (
            "# comment line\n"
            "\n"
            "uuid-a: echo hello\n"
            "   \n"
            "# another comment\n"
            "uuid-b: echo world\n"
        )
        tasks = parse_exec_md(tmp_exec_md(content))
        assert len(tasks) == 2
        assert tasks[0].uuid == "uuid-a"
        assert tasks[1].uuid == "uuid-b"

    def test_invalid_lines_skipped(self, tmp_exec_md):
        """不正行: パース不能な行が例外を投げずスキップされること"""
        content = (
            "uuid-a: echo hello\n"
            "no-colon-line\n"
            "  just some random text  \n"
            "uuid-b: echo world\n"
        )
        tasks = parse_exec_md(tmp_exec_md(content))
        assert len(tasks) == 2
        assert tasks[0].uuid == "uuid-a"
        assert tasks[1].uuid == "uuid-b"

    def test_empty_file(self, tmp_exec_md):
        """空ファイル: 空の exec.md に対して空リスト [] を返すこと"""
        tasks = parse_exec_md(tmp_exec_md(""))
        assert tasks == []

    def test_file_not_found(self, tmp_path):
        """ファイル不存在: 存在しないパスに対して FileNotFoundError を送出すること"""
        with pytest.raises(FileNotFoundError):
            parse_exec_md(tmp_path / "nonexistent.md")

    def test_annotations_dict(self, tmp_exec_md):
        """Custom annotations beyond depends/retry are captured."""
        content = "uuid-a[depends:uuid-x][retry:1][model:sonnet]: echo hello\n"
        tasks = parse_exec_md(tmp_exec_md(content))
        assert len(tasks) == 1
        assert tasks[0].annotations == {"model": "sonnet"}
