"""Tests for ghdag.pipeline — §5 acceptance criteria (issue #67)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from ghdag.pipeline import PipelineConfig, PipelineState, TemplateOrderBuilder
from ghdag.pipeline.config import ModelValidationError, resolve_models, build_agent_cmd
from ghdag.pipeline.state import status_rank, parse_frontmatter


# ---------------------------------------------------------------------------
# config.py テストケース
# ---------------------------------------------------------------------------

class TestResolveModels:
    def make_config(self, **kwargs) -> PipelineConfig:
        defaults = {"brushup": "opus", "implement": "sonnet"}
        kw = {"system_defaults": defaults, "allowed_models": {"opus", "sonnet"}}
        kw.update(kwargs)
        return PipelineConfig(**kw)

    def test_c1_default_no_override(self):
        """C1: overrides={} → system_defaults をそのまま返す"""
        cfg = self.make_config()
        result = resolve_models(cfg, {})
        assert result == {"brushup": "opus", "implement": "sonnet"}

    def test_c2_override_one_phase(self):
        """C2: overrides={"implement": "sonnet"} → implement のみ変わる"""
        cfg = self.make_config()
        result = resolve_models(cfg, {"implement": "sonnet"})
        assert result["implement"] == "sonnet"
        assert result["brushup"] == "opus"

    def test_c3_allowlist_violation(self):
        """C3: allowlist 違反 → ModelValidationError (フェーズ名 + モデル ID を含む)"""
        cfg = self.make_config(allowed_models={"opus", "sonnet"})
        with pytest.raises(ModelValidationError) as exc_info:
            resolve_models(cfg, {"brushup": "gpt-4o"})
        msg = str(exc_info.value)
        assert "brushup" in msg
        assert "gpt-4o" in msg

    def test_c4_allowlist_disabled(self):
        """C4: validate_allowlist=False → allowlist 外でも例外なし"""
        cfg = self.make_config(validate_allowlist=False)
        result = resolve_models(cfg, {"brushup": "any-model"})
        assert result["brushup"] == "any-model"

    def test_c5_build_agent_cmd_basic(self):
        """C5: build_agent_cmd 基本形"""
        cmd = build_agent_cmd(
            order_path="ts-order-uuid.md",
            result_path="ts-result-uuid.md",
            model="opus",
        )
        assert "cat queue/ts-order-uuid.md" in cmd
        assert "claude" in cmd
        assert "--model" in cmd
        assert "opus" in cmd
        assert "-p" in cmd
        assert "--dangerously-skip-permissions" in cmd
        assert "tee -a queue/ts-result-uuid.md" in cmd

    def test_c6_build_agent_cmd_shell_escape(self):
        """C6: prompt に特殊文字 → shlex.quote() でエスケープ"""
        cmd = build_agent_cmd(
            order_path="o.md",
            result_path="r.md",
            model="opus",
            prompt="it's a \"test\"",
        )
        # シェルエスケープされていること（生の引用符がそのまま入らない）
        assert "it's a \"test\"" not in cmd

    def test_c7_build_agent_cmd_gemini(self):
        """C7: agent="gemini" → コマンドに gemini が使われる"""
        cmd = build_agent_cmd(
            order_path="o.md",
            result_path="r.md",
            model="flash",
            agent="gemini",
        )
        assert "gemini" in cmd
        assert "claude" not in cmd

    def test_c8_unknown_phase_in_overrides(self):
        """C8: overrides に未知フェーズ → system_defaults のキーのみ返す"""
        cfg = self.make_config()
        result = resolve_models(cfg, {"unknown_phase": "opus"})
        assert "unknown_phase" not in result
        assert set(result.keys()) == {"brushup", "implement"}


# ---------------------------------------------------------------------------
# order.py テストケース
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
        """O2: 複数変数展開"""
        self._write_template("step", "$a and $b")
        builder = TemplateOrderBuilder(self.tmpdir)
        assert builder.build_order("step", {"a": "X", "b": "Y"}) == "X and Y"

    def test_o3_missing_variable_raises(self):
        """O3: 変数不足 → KeyError 系の例外"""
        self._write_template("step", "$name $missing")
        builder = TemplateOrderBuilder(self.tmpdir)
        with pytest.raises((KeyError, ValueError)):
            builder.build_order("step", {"name": "x"})

    def test_o4_template_order_builder_normal(self):
        """O4: TemplateOrderBuilder 正常系"""
        self._write_template("brushup", "Design: $title")
        builder = TemplateOrderBuilder(self.tmpdir)
        assert builder.build_order("brushup", {"title": "T"}) == "Design: T"

    def test_o5_template_not_found(self):
        """O5: テンプレート不存在 → FileNotFoundError"""
        builder = TemplateOrderBuilder(self.tmpdir)
        with pytest.raises(FileNotFoundError):
            builder.build_order("nonexistent", {})

    def test_o6_dollar_literal(self):
        """O6: context 値に $$ → リテラル $ として展開"""
        self._write_template("step", "Price: $price")
        builder = TemplateOrderBuilder(self.tmpdir)
        result = builder.build_order("step", {"price": "$$100"})
        # string.Template の仕様: $$ → $
        assert "$100" in result

    def test_o7_empty_context_static_template(self):
        """O7: 空コンテキスト + 変数なしテンプレート"""
        self._write_template("step", "static text")
        builder = TemplateOrderBuilder(self.tmpdir)
        assert builder.build_order("step", {}) == "static text"

    def test_order_builder_protocol(self):
        """OrderBuilder が Protocol として機能する（isinstance チェックなしでもOK）"""
        # TemplateOrderBuilder が build_order メソッドを持つことの確認
        self._write_template("s", "x")
        builder = TemplateOrderBuilder(self.tmpdir)
        assert hasattr(builder, "build_order")
        assert callable(builder.build_order)


# ---------------------------------------------------------------------------
# state.py テストケース
# ---------------------------------------------------------------------------

class TestPipelineState:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_dir = os.path.join(self.tmpdir, "pipeline-state")
        self.exec_md = os.path.join(self.tmpdir, "exec.md")
        self.queue_dir = os.path.join(self.tmpdir, "queue")
        os.makedirs(self.queue_dir, exist_ok=True)

    def make_state(self) -> PipelineState:
        return PipelineState(state_dir=self.state_dir, exec_md_path=self.exec_md)

    # --- 冪等性 ---

    def test_s1_idempotency_first_call(self):
        """S1: 初回 check_idempotency → True (未処理)"""
        st = self.make_state()
        assert st.check_idempotency("key-1") is True

    def test_s2_idempotency_after_record(self):
        """S2: record_dispatch 後 → check_idempotency → False"""
        st = self.make_state()
        st.record_dispatch("key-1")
        assert st.check_idempotency("key-1") is False

    def test_s3_idempotency_no_exec_md(self):
        """S3: exec.md 不存在 → True (未処理扱い)"""
        assert not os.path.exists(self.exec_md)
        st = self.make_state()
        assert st.check_idempotency("key-1") is True

    # --- JSON 状態永続化 ---

    def test_s4_save_and_load(self):
        """S4: save → load"""
        st = self.make_state()
        st.save("pipe-1", {"status": "running", "uuids": ["a"]})
        loaded = st.load("pipe-1")
        assert loaded == {"status": "running", "uuids": ["a"]}

    def test_s5_load_nonexistent(self):
        """S5: 存在しない pipeline_id → None"""
        st = self.make_state()
        assert st.load("nonexistent") is None

    def test_s6_save_and_remove(self):
        """S6: save → remove → True, load → None"""
        st = self.make_state()
        st.save("pipe-1", {"x": 1})
        assert st.remove("pipe-1") is True
        assert st.load("pipe-1") is None

    def test_s7_remove_nonexistent(self):
        """S7: 存在しない pipeline_id を remove → False"""
        st = self.make_state()
        assert st.remove("nonexistent") is False

    # --- exec.md 追記 ---

    def test_s13_append_exec(self):
        """S13: append_exec → exec.md 末尾に行が追記される"""
        st = self.make_state()
        st.append_exec(["uuid-a: cmd1"])
        content = Path(self.exec_md).read_text(encoding="utf-8")
        assert "uuid-a: cmd1" in content

    def test_s14_write_order_file(self):
        """S14: write_order_file → ファイルが作成され中身が正しい"""
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
        """S8: status_rank — 定義済みステータス"""
        assert status_rank("draft_done", self.STATUS_ORDER) == 2

    def test_s9_unknown_status(self):
        """S9: status_rank — 未知ステータス → -1"""
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
        """S10: parse_frontmatter 正常"""
        path = self._write("---\nstatus: draft_ready\n---\n# Body")
        assert parse_frontmatter(path) == {"status": "draft_ready"}

    def test_s11_no_frontmatter(self):
        """S11: frontmatter なし → {}"""
        path = self._write("# Body only")
        assert parse_frontmatter(path) == {}

    def test_s12_empty_file(self):
        """S12: 空ファイル → {}"""
        path = self._write("")
        assert parse_frontmatter(path) == {}
