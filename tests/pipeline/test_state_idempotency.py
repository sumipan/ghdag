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

    def test_does_not_remove_other_issue_generation_key(self, tmp_path):
        """Issue #6082 — AC1: exact issue match via structured key parse."""
        import json as _json

        from ghdag.pipeline.state import PipelineState

        exec_jsonl = tmp_path / "exec.jsonl"
        keys = ["wf:h:3", "wf:h:3:1", "wf:h:12:3", "wf:h:31", "other:h:3"]
        lines = [
            _json.dumps({"uuid": f"u{i}", "idempotency_key": k}) + "\n"
            for i, k in enumerate(keys, start=1)
        ]
        exec_jsonl.write_text("".join(lines), encoding="utf-8")
        state = PipelineState(state_dir=tmp_path / "state", exec_jsonl_path=exec_jsonl)
        removed = state.remove_idempotency_matching("wf", 3)

        assert removed == 2
        remaining_keys = []
        for line in exec_jsonl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                remaining_keys.append(_json.loads(line)["idempotency_key"])
        assert remaining_keys == ["wf:h:12:3", "wf:h:31", "other:h:3"]


class TestRemoveIdempotencyForHandler:
    def test_removes_all_generations_of_same_issue_only(self, tmp_path):
        """Issue #6082 — AC2: handler-scoped removal includes all generations."""
        import json as _json

        from ghdag.pipeline.state import PipelineState

        exec_jsonl = tmp_path / "exec.jsonl"
        keys = ["wf:h:3", "wf:h:3:1", "wf:h:12:3", "wf:g:3"]
        lines = [
            _json.dumps({"uuid": f"u{i}", "idempotency_key": k}) + "\n"
            for i, k in enumerate(keys, start=1)
        ]
        exec_jsonl.write_text("".join(lines), encoding="utf-8")
        state = PipelineState(state_dir=tmp_path / "state", exec_jsonl_path=exec_jsonl)
        removed = state.remove_idempotency_for_handler("wf", "h", 3)

        assert removed == 2
        remaining_keys = []
        for line in exec_jsonl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                remaining_keys.append(_json.loads(line)["idempotency_key"])
        assert remaining_keys == ["wf:h:12:3", "wf:g:3"]
