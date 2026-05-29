"""Tests for ghdag.files.writer (md_write)."""
import concurrent.futures
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ghdag.files import PathTraversalError, WriteResult, md_write


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    (tmp_path / "result").mkdir()
    return tmp_path


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestMdWriteBasic:
    def test_w1_write_and_audit(self, repo_root: Path) -> None:
        """W1: 正常書き込み後にファイル内容が置換され audit.jsonl に記録される"""
        f = repo_root / "result" / "bar.md"
        write_file(f, "old content")

        result = md_write("result/bar.md", "done", repo_root=repo_root)

        assert isinstance(result, WriteResult)
        assert result.path == "result/bar.md"
        assert result.bytes_written == len("done".encode("utf-8"))
        assert f.read_text(encoding="utf-8") == "done"

        audit_path = repo_root / "result" / "audit.jsonl"
        assert audit_path.exists()
        record = json.loads(audit_path.read_text(encoding="utf-8").strip())
        assert record["event"] == "md_write"
        assert record["path"] == "result/bar.md"
        assert record["bytes_written"] == len("done".encode("utf-8"))

    def test_w2_overwrite(self, repo_root: Path) -> None:
        """W2: 既存ファイルを異なる content で上書き → audit ログに 2 件記録"""
        f = repo_root / "result" / "bar.md"
        write_file(f, "first")

        md_write("result/bar.md", "second", repo_root=repo_root)
        md_write("result/bar.md", "third", repo_root=repo_root)

        assert f.read_text(encoding="utf-8") == "third"
        audit_path = repo_root / "result" / "audit.jsonl"
        lines = [line for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(lines) == 2

    def test_w3_path_traversal(self, repo_root: Path) -> None:
        """W3: repo_root 外パスで ValueError"""
        with pytest.raises(PathTraversalError, match="Path traversal"):
            md_write("../../etc/passwd", "evil", repo_root=repo_root)

    def test_w4_parent_dir_missing(self, repo_root: Path) -> None:
        """W4: 親ディレクトリが存在しない場合 FileNotFoundError（自動作成しない）"""
        with pytest.raises(FileNotFoundError):
            md_write("nonexistent_dir/foo.md", "content", repo_root=repo_root)

    def test_w5_audit_failure_nonfatal(self, repo_root: Path) -> None:
        """W5: audit 書き込み失敗時も本体の書き込みは成功する"""
        f = repo_root / "result" / "bar.md"
        write_file(f, "old")

        with patch("ghdag.files.writer.write_md_write_audit", side_effect=OSError("disk full")):
            result = md_write("result/bar.md", "new content", repo_root=repo_root)

        assert f.read_text(encoding="utf-8") == "new content"
        assert result.bytes_written == len("new content".encode("utf-8"))

    def test_w6_concurrent_no_partial_write(self, repo_root: Path) -> None:
        """TC2: 10スレッド×100回の並列 md_write でpartial writeが発生しないこと"""
        f = repo_root / "result" / "concurrent.md"
        write_file(f, "initial")

        def write_repeatedly(i: int) -> None:
            for _ in range(100):
                md_write("result/concurrent.md", f"content-{i}", repo_root=repo_root)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(write_repeatedly, i) for i in range(10)]
            for fut in futures:
                fut.result()

        final = f.read_text(encoding="utf-8")
        valid = {f"content-{i}" for i in range(10)}
        assert final in valid


class TestMdWriteProvenance:
    def test_ac14_source_and_correlation_id_recorded(self, repo_root: Path) -> None:
        """AC14: source / correlation_id が audit に記録される"""
        f = repo_root / "result" / "bar.md"
        write_file(f, "old")

        md_write(
            "result/bar.md",
            "new",
            repo_root=repo_root,
            source="cleanup_link_rewrite",
            correlation_id="xyz",
        )

        audit_path = repo_root / "result" / "audit.jsonl"
        record = json.loads(audit_path.read_text(encoding="utf-8").strip())
        assert record["source"] == "cleanup_link_rewrite"
        assert record["correlation_id"] == "xyz"

    def test_ac14_default_audit_unchanged(self, repo_root: Path) -> None:
        """AC14: デフォルト None では従来どおり source=md_write"""
        f = repo_root / "result" / "bar.md"
        write_file(f, "old")

        md_write("result/bar.md", "done", repo_root=repo_root)

        record = json.loads(
            (repo_root / "result" / "audit.jsonl").read_text(encoding="utf-8").strip()
        )
        assert record["source"] == "md_write"
        assert record.get("correlation_id") is None
