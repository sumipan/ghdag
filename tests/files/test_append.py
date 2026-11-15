"""Tests for ghdag.files.append (md_append)."""
import hashlib
from pathlib import Path

import pytest

from ghdag.core.models.files import AppendStatus, PathTraversalError
from ghdag.files.append import md_append


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    (tmp_path / "order").mkdir()
    (tmp_path / "result").mkdir()
    return tmp_path


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _hash16(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


class TestMdAppendBasic:
    def test_a6_empty_body(self, repo_root: Path) -> None:
        """A6: empty body is a valid append and returns APPENDED"""
        f = repo_root / "order" / "foo.md"
        write_file(f, "## Result\n\n")

        result = md_append("order/foo.md", "Result", "", repo_root=repo_root)

        assert result.status == AppendStatus.APPENDED
        content = f.read_text(encoding="utf-8")
        assert f"<!-- ghdag:append sha256={result.body_hash} -->" in content

    def test_a7_path_traversal(self, repo_root: Path) -> None:
        """A7: path outside repo_root raises ValueError"""
        with pytest.raises(PathTraversalError, match="Path traversal"):
            md_append("../etc/passwd", "section", "body", repo_root=repo_root)

    def test_a8_file_not_found(self, repo_root: Path) -> None:
        """A8: missing path raises FileNotFoundError"""
        with pytest.raises(FileNotFoundError):
            md_append("order/missing.md", "section", "body", repo_root=repo_root)


class TestAllowRecover:
    def test_allow_recover_false_raises_on_start_marker(self, repo_root: Path) -> None:
        """allow_recover=False: detecting a start marker raises AppendRecoverError"""
        from ghdag.files.append import AppendRecoverError
        body = "OK"
        hash16 = _hash16(body)
        f = repo_root / "order" / "foo.md"
        write_file(
            f,
            f"## Result\n\n<!-- ghdag:append:start sha256={hash16} -->\npartial content\n",
        )

        with pytest.raises(AppendRecoverError):
            md_append("order/foo.md", "Result", body, allow_recover=False, repo_root=repo_root)
