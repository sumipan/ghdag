"""Tests for ghdag.files.promote (md_promote)."""
import json
from pathlib import Path

import pytest

from ghdag.files.models import PathTraversalError, PromoteResult, PromoteStatus


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    (tmp_path / "result").mkdir()
    (tmp_path / "notes").mkdir()
    return tmp_path


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestMdPromoteBasic:
    def test_p1_promote_returns_promoted(self, repo_root: Path) -> None:
        """P1: 昇格成功時に PromoteResult(status=PROMOTED) が返る"""
        from ghdag.files import md_promote

        write_file(repo_root / "result" / "r1.md", "# Result\nsome content\n")
        write_file(repo_root / "notes" / "summary.md", "# Summary\n")

        result = md_promote(
            "result/r1.md",
            "notes/summary.md",
            repo_root=repo_root,
        )

        assert isinstance(result, PromoteResult)
        assert result.status == PromoteStatus.PROMOTED
        assert result.source_path == "result/r1.md"
        assert result.target_path == "notes/summary.md"
        assert result.section == "Promoted"

    def test_p2_content_appended_to_target(self, repo_root: Path) -> None:
        """P2: 昇格先ファイルの ## Promoted セクションにソース内容が追記される"""
        from ghdag.files import md_promote

        source_content = "# Result\nsome content\n"
        write_file(repo_root / "result" / "r1.md", source_content)
        write_file(repo_root / "notes" / "summary.md", "# Summary\n")

        md_promote("result/r1.md", "notes/summary.md", repo_root=repo_root)

        target_text = (repo_root / "notes" / "summary.md").read_text(encoding="utf-8")
        assert "## Promoted" in target_text
        assert "some content" in target_text

    def test_p3_idempotency_marker_written(self, repo_root: Path) -> None:
        """P3: 昇格後に冪等マーカが記録される"""
        from ghdag.files import md_promote

        write_file(repo_root / "result" / "r1.md", "content\n")
        write_file(repo_root / "notes" / "summary.md", "# Summary\n")

        md_promote("result/r1.md", "notes/summary.md", repo_root=repo_root)

        target_text = (repo_root / "notes" / "summary.md").read_text(encoding="utf-8")
        assert "<!-- ghdag:append key=promote:result/r1.md -->" in target_text

    def test_p4_audit_log_written(self, repo_root: Path) -> None:
        """P4: 昇格先ディレクトリの audit.jsonl に md_promote イベントが記録される"""
        from ghdag.files import md_promote

        write_file(repo_root / "result" / "r1.md", "content\n")
        write_file(repo_root / "notes" / "summary.md", "# Summary\n")

        md_promote("result/r1.md", "notes/summary.md", repo_root=repo_root)

        audit_path = repo_root / "notes" / "audit.jsonl"
        assert audit_path.exists()
        record = json.loads(audit_path.read_text(encoding="utf-8").strip())
        assert record["event"] == "md_promote"
        assert record["source_path"] == "result/r1.md"
        assert record["target_path"] == "notes/summary.md"
        assert record["section"] == "Promoted"
        assert record["status"] == "promoted"
        assert record["source"] == "md_promote"
        assert "timestamp" in record


class TestMdPromoteIdempotency:
    def test_p5_second_call_returns_noop(self, repo_root: Path) -> None:
        """P5: 同一 source_path で 2 回目の md_promote は NOOP を返す"""
        from ghdag.files import md_promote

        write_file(repo_root / "result" / "r1.md", "content\n")
        write_file(repo_root / "notes" / "summary.md", "# Summary\n")

        md_promote("result/r1.md", "notes/summary.md", repo_root=repo_root)
        result2 = md_promote("result/r1.md", "notes/summary.md", repo_root=repo_root)

        assert result2.status == PromoteStatus.NOOP

    def test_p6_second_call_no_double_append(self, repo_root: Path) -> None:
        """P6: 2 回目の md_promote 後、昇格先ファイルの内容が 1 回目と同一"""
        from ghdag.files import md_promote

        write_file(repo_root / "result" / "r1.md", "unique-body\n")
        write_file(repo_root / "notes" / "summary.md", "# Summary\n")

        md_promote("result/r1.md", "notes/summary.md", repo_root=repo_root)
        content_after_1 = (repo_root / "notes" / "summary.md").read_text(encoding="utf-8")

        md_promote("result/r1.md", "notes/summary.md", repo_root=repo_root)
        content_after_2 = (repo_root / "notes" / "summary.md").read_text(encoding="utf-8")

        assert content_after_1 == content_after_2
        assert content_after_2.count("unique-body") == 1


class TestMdPromoteCustomSection:
    def test_p7_custom_section(self, repo_root: Path) -> None:
        """P7: section 引数を指定するとその名前のセクションに追記される"""
        from ghdag.files import md_promote

        write_file(repo_root / "result" / "r1.md", "body\n")
        write_file(repo_root / "notes" / "summary.md", "# Summary\n")

        result = md_promote(
            "result/r1.md",
            "notes/summary.md",
            section="Archive",
            repo_root=repo_root,
        )

        assert result.section == "Archive"
        target_text = (repo_root / "notes" / "summary.md").read_text(encoding="utf-8")
        assert "## Archive" in target_text

    def test_p8_custom_idempotency_key(self, repo_root: Path) -> None:
        """P8: idempotency_key を明示すると、そのキーで冪等マーカが付く"""
        from ghdag.files import md_promote

        write_file(repo_root / "result" / "r1.md", "body\n")
        write_file(repo_root / "notes" / "summary.md", "# Summary\n")

        md_promote(
            "result/r1.md",
            "notes/summary.md",
            idempotency_key="custom-key",
            repo_root=repo_root,
        )

        target_text = (repo_root / "notes" / "summary.md").read_text(encoding="utf-8")
        assert "<!-- ghdag:append key=custom-key -->" in target_text


class TestMdPromoteErrors:
    def test_p9_source_not_found(self, repo_root: Path) -> None:
        """P9: source_path が存在しない場合は FileNotFoundError"""
        from ghdag.files import md_promote

        write_file(repo_root / "notes" / "summary.md", "# Summary\n")

        with pytest.raises(FileNotFoundError):
            md_promote("result/nonexistent.md", "notes/summary.md", repo_root=repo_root)

    def test_p10_path_traversal_source(self, repo_root: Path) -> None:
        """P10: source_path にパストラバーサルを指定すると ValueError"""
        from ghdag.files import md_promote

        write_file(repo_root / "notes" / "summary.md", "# Summary\n")

        with pytest.raises(PathTraversalError):
            md_promote("../../../etc/passwd", "notes/summary.md", repo_root=repo_root)

    def test_p11_path_traversal_target(self, repo_root: Path) -> None:
        """P11: target_path にパストラバーサルを指定すると ValueError"""
        from ghdag.files import md_promote

        write_file(repo_root / "result" / "r1.md", "content\n")

        with pytest.raises(PathTraversalError):
            md_promote("result/r1.md", "../../../etc/passwd", repo_root=repo_root)

    def test_p12_audit_write_failure_does_not_raise(self, repo_root: Path) -> None:
        """P12: audit 書き込み失敗でも promote 自体は成功する"""
        from unittest.mock import patch
        from ghdag.files import md_promote

        write_file(repo_root / "result" / "r1.md", "content\n")
        write_file(repo_root / "notes" / "summary.md", "# Summary\n")

        with patch("ghdag.files.promote._write_promote_audit", side_effect=OSError("disk full")):
            result = md_promote("result/r1.md", "notes/summary.md", repo_root=repo_root)

        assert result.status == PromoteStatus.PROMOTED


class TestDagHooksIntegration:
    def test_d1_protocol_has_check_promote_target(self) -> None:
        """D1: DagHooks Protocol に check_promote_target が定義されている"""
        from ghdag.dag.hooks import DagHooks
        assert hasattr(DagHooks, "check_promote_target")

    def test_d2_default_hooks_returns_none(self) -> None:
        """D2: DefaultHooks.check_promote_target() は常に None を返す"""
        from ghdag.dag.hooks import DefaultHooks
        hooks = DefaultHooks()
        assert hooks.check_promote_target("any/path.md") is None

    def test_d3_engine_calls_md_promote_when_hook_returns_target(self, repo_root: Path) -> None:
        """D3: hook が target_path を返した場合、エンジンが md_promote を呼び出す"""
        import io
        import subprocess
        import time
        from unittest.mock import MagicMock, patch
        from ghdag.dag.engine import DagEngine
        from ghdag.dag.hooks import DagHooks
        from ghdag.dag.models import DagConfig, RunningTask, Task

        result_file = repo_root / "result" / "r1.md"
        target_file = repo_root / "notes" / "summary.md"
        write_file(target_file, "# Summary\n")

        exec_file = repo_root / "exec.jsonl"
        exec_file.write_text("")
        done_dir = repo_root / "done"
        done_dir.mkdir()

        hooks = MagicMock(spec=DagHooks)
        hooks.check_rejected.return_value = False
        hooks.check_pipeline_status.return_value = None
        hooks.check_promote_target.return_value = str(target_file)

        config = DagConfig(
            exec_md_path=str(exec_file),
            exec_done_dir=str(done_dir),
            lock_file=str(repo_root / ".lock"),
        )
        engine = DagEngine(config, hooks)

        task = Task(uuid="t1", command="true", result_path=str(result_file))
        proc = subprocess.Popen(["true"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        proc.wait()
        rt = RunningTask(
            uuid="t1", task=task, proc=proc,
            started_at=time.time(), started_at_mono=time.monotonic(),
            stderr_buf=io.BytesIO(b""), retry_depth=0,
            stdout_buf=io.BytesIO(b"promoted content\n"),
        )
        engine._running["t1"] = rt

        with patch("ghdag.dag.engine.state_mark_done"):
            with patch("ghdag.files.promote.md_append") as mock_append:
                from ghdag.files.models import AppendResult, AppendStatus
                mock_append.return_value = AppendResult(
                    status=AppendStatus.APPENDED, path=str(target_file),
                    section="Promoted", body_hash="abc123",
                )
                with patch("ghdag.files.promote._write_promote_audit"):
                    engine._check_completions()

        hooks.check_promote_target.assert_called_once()

    def test_d4_engine_skips_promote_when_hook_returns_none(self, repo_root: Path) -> None:
        """D4: check_promote_target が None を返した場合、promote は実行されない"""
        import io
        import subprocess
        import time
        from unittest.mock import MagicMock, patch
        from ghdag.dag.engine import DagEngine
        from ghdag.dag.hooks import DagHooks
        from ghdag.dag.models import DagConfig, RunningTask, Task

        result_file = repo_root / "result" / "r1.md"
        exec_file = repo_root / "exec.jsonl"
        exec_file.write_text("")
        done_dir = repo_root / "done"
        done_dir.mkdir()

        hooks = MagicMock(spec=DagHooks)
        hooks.check_rejected.return_value = False
        hooks.check_pipeline_status.return_value = None
        hooks.check_promote_target.return_value = None

        config = DagConfig(
            exec_md_path=str(exec_file),
            exec_done_dir=str(done_dir),
            lock_file=str(repo_root / ".lock"),
        )
        engine = DagEngine(config, hooks)

        task = Task(uuid="t2", command="true", result_path=str(result_file))
        proc = subprocess.Popen(["true"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        proc.wait()
        rt = RunningTask(
            uuid="t2", task=task, proc=proc,
            started_at=time.time(), started_at_mono=time.monotonic(),
            stderr_buf=io.BytesIO(b""), retry_depth=0,
            stdout_buf=io.BytesIO(b"content\n"),
        )
        engine._running["t2"] = rt

        with patch("ghdag.dag.engine.state_mark_done"):
            with patch("ghdag.files.md_promote") as mock_promote:
                engine._check_completions()

        mock_promote.assert_not_called()

    def test_d5_promote_exception_does_not_fail_task(self, repo_root: Path) -> None:
        """D5: promote が例外を送出してもタスク自体は成功として扱われる"""
        import io
        import subprocess
        import time
        from unittest.mock import MagicMock, patch
        from ghdag.dag.engine import DagEngine
        from ghdag.dag.hooks import DagHooks
        from ghdag.dag.models import DagConfig, RunningTask, Task

        result_file = repo_root / "result" / "r1.md"
        exec_file = repo_root / "exec.jsonl"
        exec_file.write_text("")
        done_dir = repo_root / "done"
        done_dir.mkdir()

        hooks = MagicMock(spec=DagHooks)
        hooks.check_rejected.return_value = False
        hooks.check_pipeline_status.return_value = None
        hooks.check_promote_target.return_value = "notes/summary.md"

        config = DagConfig(
            exec_md_path=str(exec_file),
            exec_done_dir=str(done_dir),
            lock_file=str(repo_root / ".lock"),
        )
        engine = DagEngine(config, hooks)

        task = Task(uuid="t3", command="true", result_path=str(result_file))
        proc = subprocess.Popen(["true"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        proc.wait()
        rt = RunningTask(
            uuid="t3", task=task, proc=proc,
            started_at=time.time(), started_at_mono=time.monotonic(),
            stderr_buf=io.BytesIO(b""), retry_depth=0,
            stdout_buf=io.BytesIO(b"content\n"),
        )
        engine._running["t3"] = rt

        with patch("ghdag.dag.engine.state_mark_done"):
            with patch("ghdag.files.md_promote", side_effect=RuntimeError("boom")):
                engine._check_completions()

        hooks.on_task_success.assert_called_once()
