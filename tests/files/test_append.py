"""Tests for ghdag.files.append (md_append)."""
import hashlib
import threading
from pathlib import Path

import pytest

from ghdag.files import AppendResult, AppendStatus, md_append


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
        """A1: 初回 append で APPENDED が返り、セクション末尾にマーカ付きで body が追記される"""
        f = repo_root / "order" / "foo.md"
        write_file(f, "## 結果\n\n")

        result = md_append("order/foo.md", "結果", "OK", repo_root=repo_root)

        assert isinstance(result, AppendResult)
        assert result.status == AppendStatus.APPENDED
        assert result.path == "order/foo.md"
        assert result.section == "結果"
        content = f.read_text(encoding="utf-8")
        assert "OK" in content
        assert f"<!-- ghdag:append sha256={result.body_hash} -->" in content
        assert "ghdag:append:start" not in content
        assert "ghdag:append:end" not in content

    def test_a2_retry_noop(self, repo_root: Path) -> None:
        """A2: 同一 (path, section, body) の 2 回目は NOOP、ファイル内容変化なし"""
        f = repo_root / "order" / "foo.md"
        write_file(f, "## 結果\n\n")

        md_append("order/foo.md", "結果", "OK", repo_root=repo_root)
        content_after_first = f.read_text(encoding="utf-8")

        result = md_append("order/foo.md", "結果", "OK", repo_root=repo_root)
        content_after_second = f.read_text(encoding="utf-8")

        assert result.status == AppendStatus.NOOP
        assert content_after_first == content_after_second

    def test_a3_partial_write_recovery(self, repo_root: Path) -> None:
        """A3: 開始マーカのみ存在時に再実行で RECOVERED が返り、正常に追記される"""
        body = "OK"
        hash16 = _hash16(body)

        f = repo_root / "order" / "foo.md"
        write_file(
            f,
            f"## 結果\n\n<!-- ghdag:append:start sha256={hash16} -->\npartial content\n",
        )

        result = md_append("order/foo.md", "結果", body, repo_root=repo_root)

        assert result.status == AppendStatus.RECOVERED
        content = f.read_text(encoding="utf-8")
        assert "partial content" not in content
        assert "OK" in content
        assert f"<!-- ghdag:append sha256={hash16} -->" in content
        assert "ghdag:append:start" not in content

    def test_a4_concurrent_append(self, repo_root: Path) -> None:
        """A4: 2 スレッドから同時に異なる body で append → flock で直列化、両方の内容が追記される"""
        f = repo_root / "order" / "foo.md"
        write_file(f, "## 結果\n\n")

        results = []
        errors = []

        def do_append(body: str) -> None:
            try:
                r = md_append("order/foo.md", "結果", body, repo_root=repo_root)
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
        """A5: 存在しないセクション指定でファイル末尾に新規セクション作成"""
        f = repo_root / "order" / "foo.md"
        write_file(f, "# 概要\n\nsome content\n")

        result = md_append("order/foo.md", "新規セクション", "data", repo_root=repo_root)

        assert result.status == AppendStatus.APPENDED
        content = f.read_text(encoding="utf-8")
        assert "## 新規セクション" in content
        assert "data" in content

    def test_a6_empty_body(self, repo_root: Path) -> None:
        """A6: 空 body も有効な追記として APPENDED"""
        f = repo_root / "order" / "foo.md"
        write_file(f, "## 結果\n\n")

        result = md_append("order/foo.md", "結果", "", repo_root=repo_root)

        assert result.status == AppendStatus.APPENDED
        content = f.read_text(encoding="utf-8")
        assert f"<!-- ghdag:append sha256={result.body_hash} -->" in content

    def test_a7_path_traversal(self, repo_root: Path) -> None:
        """A7: repo_root 外パスで ValueError"""
        with pytest.raises(ValueError, match="Path traversal"):
            md_append("../etc/passwd", "section", "body", repo_root=repo_root)

    def test_a8_file_not_found(self, repo_root: Path) -> None:
        """A8: 存在しないパスで FileNotFoundError"""
        with pytest.raises(FileNotFoundError):
            md_append("order/missing.md", "section", "body", repo_root=repo_root)

    def test_a9_idempotency_key(self, repo_root: Path) -> None:
        """A9: idempotency_key 指定で 2 回目は NOOP、key= マーカが使われる"""
        f = repo_root / "order" / "foo.md"
        write_file(f, "## 結果\n\n")

        md_append(
            "order/foo.md", "結果", "content", idempotency_key="retry-001", repo_root=repo_root
        )
        result = md_append(
            "order/foo.md", "結果", "content", idempotency_key="retry-001", repo_root=repo_root
        )

        assert result.status == AppendStatus.NOOP
        content = f.read_text(encoding="utf-8")
        assert "<!-- ghdag:append key=retry-001 -->" in content
