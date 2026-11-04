"""Tests for ghdag.pipeline.state idempotency helpers."""

from __future__ import annotations


class TestRemoveIdempotencyMatching:
    def test_removes_matching_lines(self, tmp_path):
        import json as _json

        from ghdag.pipeline.state import PipelineState
        exec_jsonl = tmp_path / "exec.jsonl"
        exec_jsonl.write_text(
            _json.dumps({"uuid": "u1", "idempotency_key": "stash-pipeline:brushup:42"}) + "\n"
            + _json.dumps({"uuid": "u2", "idempotency_key": "stash-pipeline:impl:42"}) + "\n"
            + _json.dumps({"uuid": "u3", "idempotency_key": "stash-pipeline:merge:42"}) + "\n"
            + _json.dumps({"uuid": "u4", "idempotency_key": "stash-pipeline:brushup:99"}) + "\n"
            + _json.dumps({"uuid": "u5", "command": "cat queue/file.md | claude -p 'test'"}) + "\n",
            encoding="utf-8",
        )
        state = PipelineState(state_dir=tmp_path / "state", exec_jsonl_path=exec_jsonl)
        removed = state.remove_idempotency_matching("stash-pipeline", 42)

        assert removed == 3
        content = exec_jsonl.read_text(encoding="utf-8")
        assert "brushup:42" not in content
        assert "impl:42" not in content
        assert "merge:42" not in content
        assert "brushup:99" in content
        assert "u5" in content

    def test_returns_zero_if_no_match(self, tmp_path):
        import json as _json

        from ghdag.pipeline.state import PipelineState
        exec_jsonl = tmp_path / "exec.jsonl"
        exec_jsonl.write_text(
            _json.dumps({"uuid": "u1", "idempotency_key": "stash-pipeline:brushup:99"}) + "\n",
            encoding="utf-8",
        )
        state = PipelineState(state_dir=tmp_path / "state", exec_jsonl_path=exec_jsonl)
        removed = state.remove_idempotency_matching("stash-pipeline", 42)
        assert removed == 0

    def test_returns_zero_if_file_not_exists(self, tmp_path):
        from ghdag.pipeline.state import PipelineState
        state = PipelineState(
            state_dir=tmp_path / "state",
            exec_jsonl_path=tmp_path / "nonexistent.jsonl",
        )
        removed = state.remove_idempotency_matching("stash-pipeline", 42)
        assert removed == 0
