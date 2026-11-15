"""Tests for ghdag.files.promote (md_promote)."""
from pathlib import Path

import pytest

from ghdag.core.models.files import PathTraversalError, PromoteStatus


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    (tmp_path / "result").mkdir()
    (tmp_path / "notes").mkdir()
    return tmp_path


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestMdPromoteIdempotency:
    def test_p5_second_call_returns_noop(self, repo_root: Path) -> None:
        """P5: second md_promote with the same source_path returns NOOP"""
        from ghdag.files.promote import md_promote

        write_file(repo_root / "result" / "r1.md", "content\n")
        write_file(repo_root / "notes" / "summary.md", "# Summary\n")

        md_promote("result/r1.md", "notes/summary.md", repo_root=repo_root)
        result2 = md_promote("result/r1.md", "notes/summary.md", repo_root=repo_root)

        assert result2.status == PromoteStatus.NOOP


class TestMdPromoteErrors:
    def test_p9_source_not_found(self, repo_root: Path) -> None:
        """P9: missing source_path raises FileNotFoundError"""
        from ghdag.files.promote import md_promote

        write_file(repo_root / "notes" / "summary.md", "# Summary\n")

        with pytest.raises(FileNotFoundError):
            md_promote("result/nonexistent.md", "notes/summary.md", repo_root=repo_root)

    def test_p10_path_traversal_source(self, repo_root: Path) -> None:
        """P10: path traversal in source_path raises ValueError"""
        from ghdag.files.promote import md_promote

        write_file(repo_root / "notes" / "summary.md", "# Summary\n")

        with pytest.raises(PathTraversalError):
            md_promote("../../../etc/passwd", "notes/summary.md", repo_root=repo_root)

    def test_p11_path_traversal_target(self, repo_root: Path) -> None:
        """P11: path traversal in target_path raises ValueError"""
        from ghdag.files.promote import md_promote

        write_file(repo_root / "result" / "r1.md", "content\n")

        with pytest.raises(PathTraversalError):
            md_promote("result/r1.md", "../../../etc/passwd", repo_root=repo_root)

    def test_p12_audit_write_failure_does_not_raise(self, repo_root: Path) -> None:
        """P12: promote still succeeds when audit write fails"""
        from unittest.mock import patch

        from ghdag.files.promote import md_promote

        write_file(repo_root / "result" / "r1.md", "content\n")
        write_file(repo_root / "notes" / "summary.md", "# Summary\n")

        with patch("ghdag.files.promote._write_promote_audit", side_effect=OSError("disk full")):
            result = md_promote("result/r1.md", "notes/summary.md", repo_root=repo_root)

        assert result.status == PromoteStatus.PROMOTED


class TestDagHooksIntegration:
    def test_d1_protocol_has_check_promote_target(self) -> None:
        """D1: DagHooks Protocol defines check_promote_target"""
        from ghdag.dag.hooks import DagHooks
        assert hasattr(DagHooks, "check_promote_target")

    def test_d3_engine_calls_md_promote_when_hook_returns_target(self, repo_root: Path) -> None:
        """D3: when the hook returns target_path, the engine calls md_promote"""
        import io
        import subprocess
        from unittest.mock import MagicMock, patch

        from ghdag.dag.engine import DagEngine
        from ghdag.dag.hooks import DagHooks
        from ghdag.dag.models import DagConfig, Task, _RunningProcess

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
            exec_jsonl_path=str(exec_file),
            exec_done_dir=str(done_dir),
            lock_file=str(repo_root / ".lock"),
        )
        engine = DagEngine(config, hooks)

        task = Task(uuid="t1", command="true", result_path=str(result_file))
        proc = subprocess.Popen(["true"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        proc.wait()
        rt = _RunningProcess(
            uuid="t1", task=task, proc=proc,
            started_at=1_700_000_000.0, started_at_mono=10_000.0,
            stderr_buf=io.BytesIO(b""), retry_depth=0,
            stdout_buf=io.BytesIO(b"promoted content\n"),
        )
        engine._launcher._running["t1"] = rt

        with patch("ghdag.dag.task_launcher.state_mark_done"):
            with patch("ghdag.files.promote.md_append") as mock_append:
                from ghdag.core.models.files import AppendResult, AppendStatus
                mock_append.return_value = AppendResult(
                    status=AppendStatus.APPENDED, path=str(target_file),
                    section="Promoted", body_hash="abc123",
                )
                with patch("ghdag.files.promote._write_promote_audit"):
                    engine._launcher.check_completions()

        hooks.check_promote_target.assert_called_once()
