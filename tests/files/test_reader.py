"""Tests for ghdag.files.reader (md_read)."""
import textwrap
from pathlib import Path

import pytest

from ghdag.core.models.files import MdFile, PathTraversalError
from ghdag.files import md_read


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
    def test_file_not_found(self, repo_root: Path) -> None:
        with pytest.raises(FileNotFoundError):
            md_read("order/missing.md", repo_root=repo_root)


class TestMdReadFrontmatter:
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


class TestMdReadPathTraversal:
    def test_parent_traversal_raises(self, repo_root: Path) -> None:
        with pytest.raises(PathTraversalError, match="Path traversal detected"):
            md_read("../outside.md", repo_root=repo_root)

    def test_nested_traversal_raises(self, repo_root: Path) -> None:
        with pytest.raises(PathTraversalError, match="Path traversal detected"):
            md_read("order/../../outside.md", repo_root=repo_root)


class TestMdReadWikilink:
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
