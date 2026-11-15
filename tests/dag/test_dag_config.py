"""Tests for ghdag.dag.engine — §5.4 acceptance criteria."""


from ghdag.dag.models import DagConfig


class TestDagConfigDefaults:
    """AC 4-1, 4-2: lock_file defaults"""


    def test_max_repair_turns_defaults_to_one(self, tmp_path):
        exec_jsonl = tmp_path / "exec.jsonl"
        exec_jsonl.write_text("")
        assert DagConfig(exec_jsonl_path=str(exec_jsonl)).max_repair_turns == 1
