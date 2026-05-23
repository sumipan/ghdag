"""Tests for ghdag.files.reader (md_read)."""
import textwrap
from pathlib import Path

import pytest

from ghdag.files import MdFile, md_read


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    (tmp_path / "order").mkdir()
    (tmp_path / "result").mkdir()
    (tmp_path / "notes").mkdir()
    return tmp_path


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")


class TestMdReadBasic:
    def test_read_plain_file(self, repo_root: Path) -> None:
        write(repo_root / "order" / "foo.md", "hello world\n")
        result = md_read("order/foo.md", repo_root=repo_root)
        assert isinstance(result, MdFile)
        assert result.path == "order/foo.md"
        assert result.content == "hello world\n"
        assert result.frontmatter == {}

    def test_read_result_file(self, repo_root: Path) -> None:
        write(repo_root / "result" / "bar.md", "done\n")
        result = md_read("result/bar.md", repo_root=repo_root)
        assert result.path == "result/bar.md"
        assert result.content == "done\n"

    def test_file_not_found(self, repo_root: Path) -> None:
        with pytest.raises(FileNotFoundError):
            md_read("order/missing.md", repo_root=repo_root)


class TestMdReadFrontmatter:
    def test_frontmatter_parsed(self, repo_root: Path) -> None:
        write(
            repo_root / "order" / "with_fm.md",
            """\
            ---
            title: Test
            value: 42
            ---
            body text
            """,
        )
        result = md_read("order/with_fm.md", repo_root=repo_root)
        assert result.frontmatter == {"title": "Test", "value": 42}
        assert result.content == "body text\n"

    def test_no_frontmatter_returns_empty_dict(self, repo_root: Path) -> None:
        write(repo_root / "order" / "nofm.md", "just text\n")
        result = md_read("order/nofm.md", repo_root=repo_root)
        assert result.frontmatter == {}
        assert result.content == "just text\n"

    def test_frontmatter_only(self, repo_root: Path) -> None:
        write(
            repo_root / "order" / "fm_only.md",
            """\
            ---
            key: val
            ---
            """,
        )
        result = md_read("order/fm_only.md", repo_root=repo_root)
        assert result.frontmatter == {"key": "val"}
        assert result.content == ""

    def test_empty_frontmatter_block(self, repo_root: Path) -> None:
        write(
            repo_root / "order" / "empty_fm.md",
            """\
            ---
            ---
            content here
            """,
        )
        result = md_read("order/empty_fm.md", repo_root=repo_root)
        assert result.frontmatter == {}
        assert result.content == "content here\n"


class TestMdReadWikilink:
    def test_wikilink_resolves_to_notes(self, repo_root: Path) -> None:
        write(repo_root / "notes" / "foo.md", "from notes\n")
        result = md_read("[[foo]]", repo_root=repo_root)
        assert result.path == "notes/foo.md"
        assert result.content == "from notes\n"

    def test_wikilink_not_found_raises(self, repo_root: Path) -> None:
        with pytest.raises(FileNotFoundError):
            md_read("[[missing]]", repo_root=repo_root)


class TestMdFileDataclass:
    def test_frozen(self) -> None:
        mf = MdFile(path="a.md", frontmatter={}, content="x")
        with pytest.raises(Exception):
            mf.path = "b.md"  # type: ignore[misc]

    def test_equality(self) -> None:
        a = MdFile(path="a.md", frontmatter={"k": 1}, content="body")
        b = MdFile(path="a.md", frontmatter={"k": 1}, content="body")
        assert a == b
