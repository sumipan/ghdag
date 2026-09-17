"""Tests for ghdag.files.append (md_append)."""
import hashlib
import threading
from pathlib import Path

import pytest

from ghdag.files import AppendResult, AppendStatus, PathTraversalError, md_append


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
    def test_a1_initial_append(self, repo_root: Path) -> None:
        """A1: first append returns APPENDED and appends body with a marker at section end"""
        f = repo_root / "order" / "foo.md"
        write_file(f, "## Result\n\n")

        result = md_append("order/foo.md", "Result", "OK", repo_root=repo_root)

        assert isinstance(result, AppendResult)
        assert result.status == AppendStatus.APPENDED
        assert result.path == "order/foo.md"
        assert result.section == "Result"
        content = f.read_text(encoding="utf-8")
        assert "OK" in content
        assert f"<!-- ghdag:append sha256={result.body_hash} -->" in content
        assert "ghdag:append:start" not in content
        assert "ghdag:append:end" not in content

    def test_a2_retry_noop(self, repo_root: Path) -> None:
        """A2: second identical (path, section, body) is NOOP with no file change"""
        f = repo_root / "order" / "foo.md"
        write_file(f, "## Result\n\n")

        md_append("order/foo.md", "Result", "OK", repo_root=repo_root)
        content_after_first = f.read_text(encoding="utf-8")

        result = md_append("order/foo.md", "Result", "OK", repo_root=repo_root)
        content_after_second = f.read_text(encoding="utf-8")

        assert result.status == AppendStatus.NOOP
        assert content_after_first == content_after_second

    def test_a3_partial_write_recovery(self, repo_root: Path) -> None:
        """A3: allow_recover=True with only a start marker returns RECOVERED and appends cleanly"""
        body = "OK"
        hash16 = _hash16(body)

        f = repo_root / "order" / "foo.md"
        write_file(
            f,
            f"## Result\n\n<!-- ghdag:append:start sha256={hash16} -->\npartial content\n",
        )

        result = md_append("order/foo.md", "Result", body, allow_recover=True, repo_root=repo_root)

        assert result.status == AppendStatus.RECOVERED
        content = f.read_text(encoding="utf-8")
        assert "partial content" not in content
        assert "OK" in content
        assert f"<!-- ghdag:append sha256={hash16} -->" in content
        assert "ghdag:append:start" not in content

    def test_a4_concurrent_append(self, repo_root: Path) -> None:
        """A4: two threads appending different bodies are serialized by flock; both contents appear"""
        f = repo_root / "order" / "foo.md"
        write_file(f, "## Result\n\n")

        results = []
        errors = []

        def do_append(body: str) -> None:
            try:
                r = md_append("order/foo.md", "Result", body, repo_root=repo_root)
                results.append(r)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=do_append, args=("body-A",))
        t2 = threading.Thread(target=do_append, args=("body-B",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"threads raised: {errors}"
        assert len(results) == 2
        content = f.read_text(encoding="utf-8")
        assert "body-A" in content
        assert "body-B" in content

    def test_a5_section_not_found(self, repo_root: Path) -> None:
        """A5: missing section creates a new section at end of file"""
        f = repo_root / "order" / "foo.md"
        write_file(f, "# Overview\n\nsome content\n")

        result = md_append("order/foo.md", "New Section", "data", repo_root=repo_root)

        assert result.status == AppendStatus.APPENDED
        content = f.read_text(encoding="utf-8")
        assert "## New Section" in content
        assert "data" in content

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

    def test_a9_idempotency_key(self, repo_root: Path) -> None:
        """A9: with idempotency_key, second call is NOOP and key= marker is used"""
        f = repo_root / "order" / "foo.md"
        write_file(f, "## Result\n\n")

        md_append(
            "order/foo.md", "Result", "content", idempotency_key="retry-001", repo_root=repo_root
        )
        result = md_append(
            "order/foo.md", "Result", "content", idempotency_key="retry-001", repo_root=repo_root
        )

        assert result.status == AppendStatus.NOOP
        content = f.read_text(encoding="utf-8")
        assert "<!-- ghdag:append key=retry-001 -->" in content


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

    def test_allow_recover_false_file_unchanged(self, repo_root: Path) -> None:
        """allow_recover=False: file is unchanged on error"""
        from ghdag.files.append import AppendRecoverError
        body = "OK"
        hash16 = _hash16(body)
        f = repo_root / "order" / "foo.md"
        original_content = f"## Result\n\n<!-- ghdag:append:start sha256={hash16} -->\npartial content\n"
        write_file(f, original_content)

        with pytest.raises(AppendRecoverError):
            md_append("order/foo.md", "Result", body, allow_recover=False, repo_root=repo_root)

        assert f.read_text(encoding="utf-8") == original_content

    def test_allow_recover_true_recovers_as_before(self, repo_root: Path) -> None:
        """allow_recover=True: detecting a start marker returns RECOVERED (legacy behavior)"""
        body = "OK"
        hash16 = _hash16(body)
        f = repo_root / "order" / "foo.md"
        write_file(
            f,
            f"## Result\n\n<!-- ghdag:append:start sha256={hash16} -->\npartial content\n",
        )

        result = md_append("order/foo.md", "Result", body, allow_recover=True, repo_root=repo_root)

        assert result.status == AppendStatus.RECOVERED
        content = f.read_text(encoding="utf-8")
        assert "partial content" not in content
        assert "OK" in content

    def test_error_message_contains_path_section_marker(self, repo_root: Path) -> None:
        """AppendRecoverError message includes file path, section name, and marker content"""
        from ghdag.files.append import AppendRecoverError
        body = "OK"
        hash16 = _hash16(body)
        f = repo_root / "order" / "foo.md"
        write_file(
            f,
            f"## Result\n\n<!-- ghdag:append:start sha256={hash16} -->\npartial content\n",
        )

        with pytest.raises(AppendRecoverError) as exc_info:
            md_append("order/foo.md", "Result", body, allow_recover=False, repo_root=repo_root)

        msg = str(exc_info.value)
        assert "order/foo.md" in msg
        assert "Result" in msg
        assert "ghdag:append:start" in msg

    def test_a3_updated_requires_allow_recover_true(self, repo_root: Path) -> None:
        """A3 compat: allow_recover=True still returns RECOVERED (default-change check)"""
        body = "OK"
        hash16 = _hash16(body)
        f = repo_root / "order" / "foo.md"
        write_file(
            f,
            f"## Result\n\n<!-- ghdag:append:start sha256={hash16} -->\npartial content\n",
        )

        result = md_append("order/foo.md", "Result", body, allow_recover=True, repo_root=repo_root)

        assert result.status == AppendStatus.RECOVERED
        content = f.read_text(encoding="utf-8")
        assert "partial content" not in content
        assert f"<!-- ghdag:append sha256={hash16} -->" in content

    def test_normal_append_noop_unaffected_by_allow_recover(self, repo_root: Path) -> None:
        """Happy path (APPENDED/NOOP) is unchanged regardless of allow_recover"""
        f = repo_root / "order" / "bar.md"
        write_file(f, "## Result\n\n")

        # normal append still works with allow_recover=False
        result1 = md_append("order/bar.md", "Result", "hello", allow_recover=False, repo_root=repo_root)
        assert result1.status == AppendStatus.APPENDED

        # second call is NOOP
        result2 = md_append("order/bar.md", "Result", "hello", allow_recover=False, repo_root=repo_root)
        assert result2.status == AppendStatus.NOOP
