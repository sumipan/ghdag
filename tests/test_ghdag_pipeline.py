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

    def test_c1_default_no_override(self):
        """C1: overrides={} → return system_defaults unchanged"""
        cfg = self.make_config()
        result = resolve_models(cfg, {})
        assert result == {"brushup": "opus", "implement": "sonnet"}

    def test_c2_override_one_phase(self):
        """C2: overrides={"implement": "sonnet"} → only implement changes"""
        cfg = self.make_config()
        result = resolve_models(cfg, {"implement": "sonnet"})
        assert result["implement"] == "sonnet"
        assert result["brushup"] == "opus"

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

    def test_o1_simple_substitution(self):
        """O1: $name → world"""
        self._write_template("step", "Hello $name")
        builder = TemplateOrderBuilder(self.tmpdir)
        assert builder.build_order("step", {"name": "world"}) == "Hello world"

    def test_o2_multiple_variables(self):
        """O2: expand multiple variables"""
        self._write_template("step", "$a and $b")
        builder = TemplateOrderBuilder(self.tmpdir)
        assert builder.build_order("step", {"a": "X", "b": "Y"}) == "X and Y"

    def test_o3_missing_variable_raises(self):
        """O3: missing variable → fail with KeyError (do not leave unexpanded silently)"""
        self._write_template("step", "$name $missing")
        builder = TemplateOrderBuilder(self.tmpdir)
        with pytest.raises(KeyError):
            builder.build_order("step", {"name": "x"})

    def test_o4_template_order_builder_normal(self):
        """O4: TemplateOrderBuilder happy path"""
        self._write_template("brushup", "Design: $title")
        builder = TemplateOrderBuilder(self.tmpdir)
        assert builder.build_order("brushup", {"title": "T"}) == "Design: T"

    def test_o5_template_not_found(self):
        """O5: missing template → FileNotFoundError"""
        builder = TemplateOrderBuilder(self.tmpdir)
        with pytest.raises(FileNotFoundError):
            builder.build_order("nonexistent", {})

    def test_o6_dollar_literal(self):
        """O6: $$ in context value expands as literal $"""
        self._write_template("step", "Price: $price")
        builder = TemplateOrderBuilder(self.tmpdir)
        result = builder.build_order("step", {"price": "$$100"})
        # string.Template behavior: $$ → $
        assert "$100" in result

    def test_o7_empty_context_static_template(self):
        """O7: empty context + template with no variables"""
        self._write_template("step", "static text")
        builder = TemplateOrderBuilder(self.tmpdir)
        assert builder.build_order("step", {}) == "static text"

    def test_order_builder_protocol(self):
        """OrderBuilder works as a Protocol (OK without isinstance check)"""
        # Confirm TemplateOrderBuilder has build_order
        self._write_template("s", "x")
        builder = TemplateOrderBuilder(self.tmpdir)
        assert hasattr(builder, "build_order")
        assert callable(builder.build_order)


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

    def test_quota_gate_reused_across_append_exec_records(self):
        """AC-3: QuotaGate is created once when PipelineState is built and reused."""
        st = self.make_state()
        gate1 = st._quota_gate
        st.append_exec_records([{"uuid": "u1", "command": "echo 1"}])
        gate2 = st._quota_gate
        st.append_exec_records([{"uuid": "u2", "command": "echo 2"}])
        gate3 = st._quota_gate
        assert gate1 is gate2 is gate3

    # --- idempotency ---

    def test_s1_idempotency_first_call(self):
        """S1: first check_idempotency → True (unprocessed)"""
        st = self.make_state()
        assert st.check_idempotency("key-1") is True

    def test_s2_idempotency_after_record(self):
        """S2: check_idempotency is False when idempotency_key exists in JSONL"""
        import json
        exec_jsonl = os.path.join(self.tmpdir, "exec.jsonl")
        st = PipelineState(state_dir=self.state_dir, exec_jsonl_path=exec_jsonl)
        Path(exec_jsonl).write_text(
            json.dumps({"idempotency_key": "key-1", "uuid": "abc-123"}) + "\n",
            encoding="utf-8",
        )
        assert st.check_idempotency("key-1") is False

    def test_s3_idempotency_no_exec_md(self):
        """S3: missing exec.md → True (treated as unprocessed)"""
        assert not os.path.exists(self.exec_md)
        st = self.make_state()
        assert st.check_idempotency("key-1") is True

    # --- JSON state persistence ---

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

    # --- append to exec.md ---

    def test_s14_write_order_file(self):
        """S14: write_order_file → file is created with correct contents"""
        st = self.make_state()
        filename = st.write_order_file(
            ts="20260410",
            order_uuid="abc",
            content="order body",
            queue_dir=self.queue_dir,
        )
        assert filename == "20260410-claude-order-abc.md"
        content = Path(self.queue_dir, filename).read_text(encoding="utf-8")
        assert content == "order body"


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


class TestFromRepoRoot:
    def test_ac4_from_repo_root_paths(self, tmp_path):
        """AC-4: from_repo_root → state_dir and exec_jsonl_path use standard paths"""
        state = PipelineState.from_repo_root(tmp_path)
        assert state._state_dir == tmp_path / ".pipeline-state"
        assert state._exec_jsonl_path == tmp_path / "jobs" / "exec.jsonl"

    def test_ac4_from_repo_root_str(self, tmp_path):
        """AC-4: from_repo_root accepts str and yields Path"""
        state = PipelineState.from_repo_root(str(tmp_path))
        assert state._state_dir == tmp_path / ".pipeline-state"
        assert state._exec_jsonl_path == tmp_path / "jobs" / "exec.jsonl"


# ---------------------------------------------------------------------------
# JSONL mode: parse_exec_tasks / remove_exec_entries (AC1, AC2)
# ---------------------------------------------------------------------------


class TestParseExecTasksJsonl:
    def make_state(self, tmp_path) -> PipelineState:
        return PipelineState(
            state_dir=tmp_path / ".pipeline-state",
            exec_jsonl_path=tmp_path / "queue" / "exec.jsonl",
        )

    def test_ac1_1_normal(self, tmp_path):
        """AC1-1: valid 2-line JSONL → {uuid: command} dict"""
        jsonl = tmp_path / "queue" / "exec.jsonl"
        jsonl.parent.mkdir(parents=True)
        jsonl.write_text(
            _json.dumps({"uuid": "abc-123", "command": "echo hello", "depends": []}) + "\n"
            + _json.dumps({"uuid": "def-456", "command": "echo world", "depends": []}) + "\n",
            encoding="utf-8",
        )
        state = self.make_state(tmp_path)
        assert state.parse_exec_tasks() == {"abc-123": "echo hello", "def-456": "echo world"}

    def test_ac1_2_empty_file(self, tmp_path):
        """AC1-2: empty file → {}"""
        jsonl = tmp_path / "queue" / "exec.jsonl"
        jsonl.parent.mkdir(parents=True)
        jsonl.write_text("", encoding="utf-8")
        state = self.make_state(tmp_path)
        assert state.parse_exec_tasks() == {}

    def test_ac1_3_file_not_found(self, tmp_path):
        """AC1-3: missing file → {}"""
        state = self.make_state(tmp_path)
        assert state.parse_exec_tasks() == {}

    def test_ac1_4_idempotency_key_skipped(self, tmp_path):
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

    def test_ac1_5_broken_line_skipped(self, tmp_path):
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

    def _write_jsonl(self, path: Path, records: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(_json.dumps(r) for r in records) + "\n",
            encoding="utf-8",
        )

    def test_ac2_1_remove_two_of_three(self, tmp_path):
        """AC2-1: delete 2 of 3 lines → 1 remains, return 2"""
        jsonl = tmp_path / "queue" / "exec.jsonl"
        self._write_jsonl(jsonl, [
            {"uuid": "a", "command": "cmd-a"},
            {"uuid": "b", "command": "cmd-b"},
            {"uuid": "c", "command": "cmd-c"},
        ])
        state = self.make_state(tmp_path)
        removed = state.remove_exec_entries({"a", "c"})
        assert removed == 2
        content = jsonl.read_text(encoding="utf-8")
        assert "cmd-b" in content
        assert "cmd-a" not in content
        assert "cmd-c" not in content

    def test_ac2_2_idempotency_line_preserved(self, tmp_path):
        """AC2-2: keep idempotency lines; delete only uuid lines"""
        jsonl = tmp_path / "queue" / "exec.jsonl"
        self._write_jsonl(jsonl, [
            {"uuid": "a", "command": "cmd-a"},
            {"idempotency_key": "workflow:phase:1"},
        ])
        state = self.make_state(tmp_path)
        removed = state.remove_exec_entries({"a"})
        assert removed == 1
        content = jsonl.read_text(encoding="utf-8")
        assert "idempotency_key" in content
        assert "cmd-a" not in content

    def test_ac2_3_no_match(self, tmp_path):
        """AC2-3: no matching UUID → unchanged, return 0"""
        jsonl = tmp_path / "queue" / "exec.jsonl"
        self._write_jsonl(jsonl, [
            {"uuid": "a", "command": "cmd-a"},
            {"uuid": "b", "command": "cmd-b"},
        ])
        original = jsonl.read_text(encoding="utf-8")
        state = self.make_state(tmp_path)
        removed = state.remove_exec_entries({"x"})
        assert removed == 0
        assert jsonl.read_text(encoding="utf-8") == original

    def test_ac2_4_file_not_found(self, tmp_path):
        """AC2-4: missing file → return 0 with no error"""
        state = self.make_state(tmp_path)
        assert state.remove_exec_entries({"a"}) == 0
