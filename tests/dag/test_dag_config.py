"""Tests for ghdag.dag.engine — §5.4 acceptance criteria."""

from pathlib import Path

from ghdag.dag.models import DagConfig


class TestDagConfigDefaults:
    """AC 4-1, 4-2: lock_file defaults"""

    def test_lock_file_defaults_to_exec_md_parent(self, tmp_path):
        """4-1: without lock_file, .ghdag.lock is created under exec_jsonl_path parent"""
        exec_jsonl = tmp_path / "jobs" / "exec.jsonl"
        exec_jsonl.parent.mkdir(parents=True, exist_ok=True)
        exec_jsonl.write_text("")
        config = DagConfig(exec_jsonl_path=str(exec_jsonl))
        assert config.lock_file == Path(str(exec_jsonl.parent)) / ".ghdag.lock"

    def test_lock_file_explicit_preserved(self, tmp_path):
        """4-2: explicitly set lock_file is preserved (backward compatible)"""
        exec_jsonl = tmp_path / "exec.jsonl"
        exec_jsonl.write_text("")
        custom = str(tmp_path / "custom.lock")
        config = DagConfig(exec_jsonl_path=str(exec_jsonl), lock_file=custom)
        assert config.lock_file == Path(custom)
