"""Tests for ghdag.pipeline — §5 acceptance criteria (issue #67)."""

from __future__ import annotations

import json as _json
import os
import tempfile
from pathlib import Path

import pytest

from ghdag.pipeline import PipelineConfig, PipelineState, TemplateOrderBuilder
from ghdag.pipeline.config import ModelValidationError, resolve_models
from ghdag.pipeline.state import parse_frontmatter, status_rank

# ---------------------------------------------------------------------------
# config.py test cases
# ---------------------------------------------------------------------------

class TestResolveModels:
    def make_config(self, **kwargs) -> PipelineConfig:
        defaults = {"brushup": "opus", "implement": "sonnet"}
        kw = {"system_defaults": defaults, "allowed_models": {"opus", "sonnet"}}
        kw.update(kwargs)
        return PipelineConfig(**kw)

    def test_c3_allowlist_violation(self):
        """C3: allowlist violation → ModelValidationError (includes phase name + model ID)"""
        cfg = self.make_config(allowed_models={"opus", "sonnet"})
        with pytest.raises(ModelValidationError) as exc_info:
            resolve_models(cfg, {"brushup": "gpt-4o"})
        msg = str(exc_info.value)
        assert "brushup" in msg
        assert "gpt-4o" in msg

    def test_c4_allowlist_disabled(self):
        """C4: validate_allowlist=False → no exception outside allowlist"""
        cfg = self.make_config(validate_allowlist=False)
        result = resolve_models(cfg, {"brushup": "any-model"})
        assert result["brushup"] == "any-model"

    def test_c8_unknown_phase_in_overrides(self):
        """C8: unknown phase in overrides → return only system_defaults keys"""
        cfg = self.make_config()
        result = resolve_models(cfg, {"unknown_phase": "opus"})
        assert "unknown_phase" not in result
        assert set(result.keys()) == {"brushup", "implement"}


# ---------------------------------------------------------------------------
# order.py test cases
# ---------------------------------------------------------------------------

class TestTemplateOrderBuilder:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()

    def _write_template(self, name: str, content: str):
        Path(self.tmpdir, f"{name}.md").write_text(content, encoding="utf-8")

    def test_o5_template_not_found(self):
        """O5: missing template → FileNotFoundError"""
        builder = TemplateOrderBuilder(self.tmpdir)
        with pytest.raises(FileNotFoundError):
            builder.build_order("nonexistent", {})


# ---------------------------------------------------------------------------
# state.py test cases
# ---------------------------------------------------------------------------

class TestPipelineState:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_dir = os.path.join(self.tmpdir, "pipeline-state")
        self.exec_md = os.path.join(self.tmpdir, "exec.jsonl")
        self.queue_dir = os.path.join(self.tmpdir, "queue")
        os.makedirs(self.queue_dir, exist_ok=True)

    def make_state(self) -> PipelineState:
        return PipelineState(state_dir=self.state_dir, exec_jsonl_path=self.exec_md)

    def test_s1_idempotency_first_call(self):
        """S1: first check_idempotency → True (unprocessed)"""
        st = self.make_state()
        assert st.check_idempotency("key-1") is True

    def test_s4_save_and_load(self):
        """S4: save → load"""
        st = self.make_state()
        st.save("pipe-1", {"status": "running", "uuids": ["a"]})
        loaded = st.load("pipe-1")
        assert loaded == {"status": "running", "uuids": ["a"]}

    def test_s5_load_nonexistent(self):
        """S5: nonexistent pipeline_id → None"""
        st = self.make_state()
        assert st.load("nonexistent") is None

    def test_s6_save_and_remove(self):
        """S6: save → remove → True, load → None"""
        st = self.make_state()
        st.save("pipe-1", {"x": 1})
        assert st.remove("pipe-1") is True
        assert st.load("pipe-1") is None

    def test_s7_remove_nonexistent(self):
        """S7: remove nonexistent pipeline_id → False"""
        st = self.make_state()
        assert st.remove("nonexistent") is False


class TestStatusRank:
    STATUS_ORDER = ("draft_ready", "draft_running", "draft_done")

    def test_s8_known_status(self):
        """S8: status_rank — defined statuses"""
        assert status_rank("draft_done", self.STATUS_ORDER) == 2

    def test_s9_unknown_status(self):
        """S9: status_rank — unknown status → -1"""
        assert status_rank("unknown", self.STATUS_ORDER) == -1


class TestParseFrontmatter:
    def _write(self, content: str) -> str:
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        )
        f.write(content)
        f.close()
        return f.name

    def test_s10_normal(self):
        """S10: parse_frontmatter happy path"""
        path = self._write("---\nstatus: draft_ready\n---\n# Body")
        assert parse_frontmatter(path) == {"status": "draft_ready"}

    def test_s11_no_frontmatter(self):
        """S11: no frontmatter → {}"""
        path = self._write("# Body only")
        assert parse_frontmatter(path) == {}

    def test_s12_empty_file(self):
        """S12: empty file → {}"""
        path = self._write("")
        assert parse_frontmatter(path) == {}


# ---------------------------------------------------------------------------
# JSONL mode: parse_exec_tasks / remove_exec_entries (AC1, AC2)
# ---------------------------------------------------------------------------


class TestParseExecTasksJsonl:
    def make_state(self, tmp_path) -> PipelineState:
        return PipelineState(
            state_dir=tmp_path / ".pipeline-state",
            exec_jsonl_path=tmp_path / "queue" / "exec.jsonl",
        )

    def test_parse_exec_tasks_missing_file_returns_empty(self, tmp_path):
        """AC1-3: missing file → {}"""
        state = self.make_state(tmp_path)
        assert state.parse_exec_tasks() == {}

    def test_parse_exec_tasks_skips_idempotency_key_lines(self, tmp_path):
        """AC1-4: skip idempotency_key-only lines; return uuid lines"""
        jsonl = tmp_path / "queue" / "exec.jsonl"
        jsonl.parent.mkdir(parents=True)
        jsonl.write_text(
            _json.dumps({"idempotency_key": "workflow:phase:123"}) + "\n"
            + _json.dumps({"uuid": "abc-123", "command": "echo hi"}) + "\n",
            encoding="utf-8",
        )
        state = self.make_state(tmp_path)
        assert state.parse_exec_tasks() == {"abc-123": "echo hi"}

    def test_parse_exec_tasks_skips_invalid_json_lines(self, tmp_path):
        """AC1-5: skip broken JSON lines; return valid lines"""
        jsonl = tmp_path / "queue" / "exec.jsonl"
        jsonl.parent.mkdir(parents=True)
        jsonl.write_text(
            "not-json\n"
            + _json.dumps({"uuid": "abc-123", "command": "echo ok"}) + "\n",
            encoding="utf-8",
        )
        state = self.make_state(tmp_path)
        assert state.parse_exec_tasks() == {"abc-123": "echo ok"}


class TestRemoveExecEntriesJsonl:
    def make_state(self, tmp_path) -> PipelineState:
        return PipelineState(
            state_dir=tmp_path / ".pipeline-state",
            exec_jsonl_path=tmp_path / "queue" / "exec.jsonl",
        )

    def test_remove_exec_entries_missing_file_returns_zero(self, tmp_path):
        """AC2-4: missing file → return 0 with no error"""
        state = self.make_state(tmp_path)
        assert state.remove_exec_entries({"a"}) == 0
