"""Tests for ghdag.files.writer (md_write)."""
from pathlib import Path
from unittest.mock import patch

import pytest

from ghdag.core.models.files import PathTraversalError
from ghdag.files import md_write


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    (tmp_path / "result").mkdir()
    return tmp_path


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestMdWriteBasic:


    def test_w3_path_traversal(self, repo_root: Path) -> None:
        """W3: path outside repo_root raises ValueError"""
        with pytest.raises(PathTraversalError, match="Path traversal"):
            md_write("../../etc/passwd", "evil", repo_root=repo_root)

    def test_w4_parent_dir_missing(self, repo_root: Path) -> None:
        """W4: missing parent directory raises FileNotFoundError (no auto-create)"""
        with pytest.raises(FileNotFoundError):
            md_write("nonexistent_dir/foo.md", "content", repo_root=repo_root)

    def test_w5_audit_failure_nonfatal(self, repo_root: Path) -> None:
        """W5: main write still succeeds when audit write fails"""
        f = repo_root / "result" / "bar.md"
        write_file(f, "old")

        with patch("ghdag.files.writer.write_md_write_audit", side_effect=OSError("disk full")):
            result = md_write("result/bar.md", "new content", repo_root=repo_root)

        assert f.read_text(encoding="utf-8") == "new content"
        assert result.bytes_written == len("new content".encode("utf-8"))


