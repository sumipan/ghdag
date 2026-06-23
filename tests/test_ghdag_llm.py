"""Tests for ghdag.llm — ワンショット LLM 呼び出しインタフェース"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ghdag.llm import (
    DANGEROUS_FULL_ACCESS,
    ENGINE_DEFAULTS,
    ENGINE_SPECS,
    JSON_ONLY,
    TEXT_ONLY,
    WEB_RESEARCH,
    EngineModelError,
    EngineSpec,
    LLMCapabilities,
    LLMParseError,
    LLMResult,
    build_llm_cmd,
    call,
    list_engines,
    list_models,
    validate_engine_model,
)
from ghdag.llm.spec import render_exec_command
from ghdag.workflow.engine import get_adapter

# ---------------------------------------------------------------------------
# ホワイトリスト・検証
# ---------------------------------------------------------------------------

class TestEngineModels:
    def test_list_engines(self):
        """利用可能なエンジン一覧が返る"""
        engines = list_engines()
        assert "claude" in engines
        assert "gemini" in engines
        assert "cursor" in engines
        assert engines == sorted(engines)

    def test_list_models_claude(self):
        """claude エンジンの許可モデル一覧"""
        models = list_models("claude")
        assert "claude-sonnet-4-6" in models
        assert "claude-opus-4-6" in models
        assert models == sorted(models)

    def test_list_models_gemini(self, monkeypatch):
        """gemini エンジンのデフォルト許可モデル一覧（GHDAG_LLM_MODELS 環境変数の影響を除外）"""
        import ghdag.llm.engines as engines_mod
        from ghdag.llm._constants import DEFAULT_ENGINE_MODELS
        monkeypatch.setattr(engines_mod, "ENGINE_MODELS", DEFAULT_ENGINE_MODELS)
        models = list_models("gemini")
        assert "gemini-2.5-flash" in models
        assert "gemini-2.5-pro" in models

    def test_list_models_cursor(self):
        """cursor エンジンの許可モデル一覧"""
        models = list_models("cursor")
        assert "auto" in models
        assert "composer-2" in models

    def test_list_models_unknown_engine(self):
        """未知エンジン → EngineModelError"""
        with pytest.raises(EngineModelError, match="Unknown engine"):
            list_models("openai")


class TestValidateEngineModel:
    def test_valid_claude_model(self):
        """claude + 許可モデル → そのまま返る"""
        result = validate_engine_model("claude", "claude-opus-4-6")
        assert result == "claude-opus-4-6"

    def test_valid_gemini_model(self, monkeypatch):
        """gemini + 許可モデル → そのまま返る（GHDAG_LLM_MODELS 環境変数の影響を除外）"""
        import ghdag.llm.engines as engines_mod
        from ghdag.llm._constants import DEFAULT_ENGINE_MODELS
        monkeypatch.setattr(engines_mod, "ENGINE_MODELS", DEFAULT_ENGINE_MODELS)
        result = validate_engine_model("gemini", "gemini-2.5-pro")
        assert result == "gemini-2.5-pro"

    def test_default_model_claude(self):
        """model=None → エンジンデフォルト"""
        result = validate_engine_model("claude", None)
        assert result == ENGINE_DEFAULTS["claude"]

    def test_default_model_gemini(self):
        """model=None → エンジンデフォルト"""
        result = validate_engine_model("gemini", None)
        assert result == ENGINE_DEFAULTS["gemini"]

    def test_valid_cursor_model(self):
        """cursor + 許可モデル → そのまま返る"""
        result = validate_engine_model("cursor", "composer-2")
        assert result == "composer-2"

    def test_default_model_cursor(self):
        """cursor + model=None → "auto" がデフォルト"""
        result = validate_engine_model("cursor", None)
        assert result == ENGINE_DEFAULTS["cursor"]
        assert result == "auto"

    def test_unknown_engine(self):
        """未知エンジン → EngineModelError"""
        with pytest.raises(EngineModelError, match="Unknown engine"):
            validate_engine_model("openai", "gpt-4o")

    def test_invalid_model(self):
        """許可外モデル → EngineModelError"""
        with pytest.raises(EngineModelError, match="Model not in allowlist"):
            validate_engine_model("claude", "gpt-4o")

    def test_invalid_model_contains_info(self):
        """エラーメッセージにエンジン名とモデル名が含まれる"""
        with pytest.raises(EngineModelError) as exc_info:
            validate_engine_model("claude", "unknown-model")
        msg = str(exc_info.value)
        assert "claude" in msg
        assert "unknown-model" in msg


# ---------------------------------------------------------------------------
# コマンド構築
# ---------------------------------------------------------------------------

class TestBuildLLMCmd:
    def test_basic_claude(self):
        """基本的な claude コマンド構築（capabilities フラグ含む）"""
        cmd = build_llm_cmd("claude", "claude-opus-4-6", "hello")
        # 基本要素の確認
        assert cmd[0] == "claude"
        assert "--model" in cmd
        assert "claude-opus-4-6" in cmd
        assert "-p" in cmd
        assert "hello" in cmd
        # TEXT_ONLY デフォルト: permission-mode と disallowed-tools が付く
        assert "--permission-mode" in cmd
        assert "--disallowed-tools" in cmd

    def test_basic_gemini(self):
        """基本的な gemini コマンド構築（capabilities フラグなし）"""
        cmd = build_llm_cmd("gemini", "gemini-2.5-flash", "hello")
        assert cmd == ["gemini", "--model", "gemini-2.5-flash", "-p", "hello"]

    def test_basic_cursor(self):
        """基本的な cursor コマンド構築（CLI は agent、capabilities フラグなし）"""
        cmd = build_llm_cmd("cursor", "composer-2", "hello")
        assert cmd == ["agent", "--model", "composer-2", "-p", "hello"]


# ---------------------------------------------------------------------------
# LLMCapabilities
# ---------------------------------------------------------------------------

class TestCapabilities:
    def test_text_only_preset(self):
        assert TEXT_ONLY.permission_mode == "default"
        assert TEXT_ONLY.output_format == "text"
        assert "Bash" in TEXT_ONLY.disallowed_tools
        assert "Edit" in TEXT_ONLY.disallowed_tools
        assert "Write" in TEXT_ONLY.disallowed_tools
        assert "NotebookEdit" in TEXT_ONLY.disallowed_tools
        assert "WebFetch" in TEXT_ONLY.disallowed_tools
        assert "WebSearch" in TEXT_ONLY.disallowed_tools

    def test_json_only_preset(self):
        assert JSON_ONLY.permission_mode == "default"
        assert JSON_ONLY.output_format == "json"
        assert "Bash" in JSON_ONLY.disallowed_tools

    def test_web_research_preset(self):
        assert WEB_RESEARCH.permission_mode == "default"
        assert WEB_RESEARCH.output_format == "text"
        assert "WebFetch" in WEB_RESEARCH.allowed_tools
        assert "WebSearch" in WEB_RESEARCH.allowed_tools
        assert "Read" in WEB_RESEARCH.allowed_tools
        assert "Grep" in WEB_RESEARCH.allowed_tools
        assert "Glob" in WEB_RESEARCH.allowed_tools
        assert "Bash" in WEB_RESEARCH.disallowed_tools
        assert "Edit" in WEB_RESEARCH.disallowed_tools

    def test_dangerous_full_access_preset(self):
        assert DANGEROUS_FULL_ACCESS.permission_mode == "bypassPermissions"
        assert len(DANGEROUS_FULL_ACCESS.disallowed_tools) == 0
        assert len(DANGEROUS_FULL_ACCESS.allowed_tools) == 0

    def test_frozen(self):
        import dataclasses
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            TEXT_ONLY.permission_mode = "bypassPermissions"  # type: ignore

    def test_custom_capabilities(self):
        caps = LLMCapabilities(permission_mode="plan", output_format="json")
        assert caps.permission_mode == "plan"
        assert caps.output_format == "json"

    def test_force_cursor(self):
        """cursor で dangerously_skip_permissions=True のとき --force が付与される"""
        cmd = build_llm_cmd(
            "cursor", "auto", "hello",
            dangerously_skip_permissions=True,
        )
        assert "--force" in cmd

    def test_force_cursor_not_added_by_default(self):
        """cursor で dangerously_skip_permissions=False のとき --force は付与されない"""
        cmd = build_llm_cmd("cursor", "auto", "hello")
        assert "--force" not in cmd


# ---------------------------------------------------------------------------
# LLMResult
# ---------------------------------------------------------------------------

class TestLLMResult:
    def test_ok_success(self):
        r = LLMResult(stdout="out", stderr="", returncode=0)
        assert r.ok is True

    def test_ok_failure(self):
        r = LLMResult(stdout="", stderr="err", returncode=1)
        assert r.ok is False


class TestLLMResultValidate:
    def test_json_ok_valid_json(self):
        """JSON_ONLY + 有効な JSON → LLMParseError なし"""
        r = LLMResult(stdout='{"k": "v"}', stderr="", returncode=0)
        result = r.validate(JSON_ONLY)
        assert result is r  # 自身を返す

    def test_json_ok_invalid_json(self):
        """JSON_ONLY + non-JSON → LLMParseError"""
        r = LLMResult(stdout="not json", stderr="", returncode=0)
        with pytest.raises(LLMParseError) as exc_info:
            r.validate(JSON_ONLY)
        assert exc_info.value.raw == "not json"

    def test_json_fail_skips_validation(self):
        """returncode != 0 → 検証スキップ"""
        r = LLMResult(stdout="err", stderr="", returncode=1)
        result = r.validate(JSON_ONLY)  # should not raise
        assert result is r

    def test_text_ok_no_validation(self):
        """TEXT_ONLY → JSON 検証なし"""
        r = LLMResult(stdout="not json", stderr="", returncode=0)
        result = r.validate(TEXT_ONLY)  # should not raise
        assert result is r


# ---------------------------------------------------------------------------
# build_llm_cmd with capabilities (new API)
# ---------------------------------------------------------------------------

class TestBuildLLMCmdCapabilities:
    def test_text_only_default(self):
        """TEXT_ONLY: --permission-mode default --disallowed-tools Bash,Edit,Write,NotebookEdit,WebFetch,WebSearch"""
        cmd = build_llm_cmd("claude", "claude-sonnet-4-6", "hello", capabilities=TEXT_ONLY)
        assert "--permission-mode" in cmd
        assert "default" in cmd
        assert "--disallowed-tools" in cmd
        disallowed_idx = cmd.index("--disallowed-tools")
        disallowed_val = cmd[disallowed_idx + 1]
        for tool in ("Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch"):
            assert tool in disallowed_val
        assert "--allowed-tools" not in cmd
        assert "--output-format" not in cmd  # text is default, not passed

    def test_json_only(self):
        """JSON_ONLY: --output-format json and disallowed-tools"""
        cmd = build_llm_cmd("claude", "claude-sonnet-4-6", "hello", capabilities=JSON_ONLY)
        assert "--output-format" in cmd
        assert "json" in cmd
        assert "--disallowed-tools" in cmd
        assert "--permission-mode" in cmd

    def test_web_research(self):
        """WEB_RESEARCH: --allowed-tools and --disallowed-tools both present"""
        cmd = build_llm_cmd("claude", "claude-sonnet-4-6", "hello", capabilities=WEB_RESEARCH)
        assert "--allowed-tools" in cmd
        allowed_idx = cmd.index("--allowed-tools")
        allowed_val = cmd[allowed_idx + 1]
        for tool in ("WebFetch", "WebSearch", "Read", "Grep", "Glob"):
            assert tool in allowed_val
        assert "--disallowed-tools" in cmd

    def test_dangerous_full_access(self):
        """DANGEROUS_FULL_ACCESS: --permission-mode bypassPermissions, no allowed/disallowed"""
        cmd = build_llm_cmd("claude", "claude-sonnet-4-6", "hello", capabilities=DANGEROUS_FULL_ACCESS)
        assert "--permission-mode" in cmd
        perm_idx = cmd.index("--permission-mode")
        assert cmd[perm_idx + 1] == "bypassPermissions"
        assert "--allowed-tools" not in cmd
        assert "--disallowed-tools" not in cmd

    def test_custom_permission_mode(self):
        """カスタム permission_mode"""
        caps = LLMCapabilities(permission_mode="plan")
        cmd = build_llm_cmd("claude", "claude-sonnet-4-6", "hello", capabilities=caps)
        assert "--permission-mode" in cmd
        perm_idx = cmd.index("--permission-mode")
        assert cmd[perm_idx + 1] == "plan"

    def test_gemini_ignores_capabilities_flags(self):
        """gemini では capabilities フラグは付与されない"""
        caps = LLMCapabilities(permission_mode="default", output_format="text")
        cmd = build_llm_cmd("gemini", "gemini-2.5-flash", "hello", capabilities=caps)
        assert "--permission-mode" not in cmd
        assert "--disallowed-tools" not in cmd


# ---------------------------------------------------------------------------
# _validate_capabilities_for_engine
# ---------------------------------------------------------------------------

class TestValidateCapabilitiesForEngine:
    @patch("ghdag.llm.engines.subprocess.run")
    def test_gemini_with_disallowed_tools_raises(self, mock_run):
        with pytest.raises(NotImplementedError, match="disallowed_tools"):
            call("hello", engine="gemini", capabilities=TEXT_ONLY)

    @patch("ghdag.llm.engines.subprocess.run")
    def test_gemini_with_permission_mode_raises(self, mock_run):
        with pytest.raises(NotImplementedError, match="permission_mode"):
            call("hello", engine="gemini", capabilities=DANGEROUS_FULL_ACCESS)

    @patch("ghdag.llm.engines.subprocess.run")
    def test_cursor_with_allowed_tools_raises(self, mock_run):
        with pytest.raises(NotImplementedError):
            call("hello", engine="cursor", capabilities=WEB_RESEARCH)

    @patch("ghdag.llm.engines.subprocess.run")
    def test_cursor_with_text_only_ok(self, mock_run):
        """cursor engine で TEXT_ONLY (disallowed_tools あり) が通ること"""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        result = call("hello", engine="cursor", capabilities=TEXT_ONLY)
        assert result.ok

    @patch("ghdag.llm.engines.subprocess.run")
    def test_claude_with_text_only_ok(self, mock_run):
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        result = call("hello", engine="claude", capabilities=TEXT_ONLY)
        assert result.ok


# ---------------------------------------------------------------------------
# call() — subprocess をモック
# ---------------------------------------------------------------------------

class TestCall:
    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_default_model(self, mock_run: MagicMock):
        """model=None → デフォルトモデルで呼び出し"""
        mock_run.return_value = MagicMock(
            stdout="response", stderr="", returncode=0,
        )
        result = call("hello", engine="claude")
        assert result.ok
        assert result.stdout == "response"
        cmd = mock_run.call_args[0][0]
        assert "--model" in cmd
        assert ENGINE_DEFAULTS["claude"] in cmd

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_explicit_model(self, mock_run: MagicMock):
        """明示的モデル指定"""
        mock_run.return_value = MagicMock(
            stdout="ok", stderr="", returncode=0,
        )
        result = call("hello", engine="claude", model="claude-opus-4-6")
        assert result.ok
        cmd = mock_run.call_args[0][0]
        assert "claude-opus-4-6" in cmd

    def test_call_invalid_model(self):
        """許可外モデル → EngineModelError（subprocess 呼び出し前にエラー）"""
        with pytest.raises(EngineModelError):
            call("hello", engine="claude", model="bad-model")

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_with_stdin(self, mock_run: MagicMock):
        """stdin_text が渡される"""
        mock_run.return_value = MagicMock(
            stdout="ok", stderr="", returncode=0,
        )
        call("hello", engine="claude", stdin_text="input data")
        _, kwargs = mock_run.call_args
        assert kwargs["input"] == "input data"

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_with_timeout(self, mock_run: MagicMock):
        """timeout が渡される"""
        mock_run.return_value = MagicMock(
            stdout="ok", stderr="", returncode=0,
        )
        call("hello", engine="claude", timeout=30)
        _, kwargs = mock_run.call_args
        assert kwargs["timeout"] == 30

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_action_none_default_no_dangerously(self, mock_run: MagicMock):
        """デフォルト capabilities では --dangerously-skip-permissions は付与されない"""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("test prompt", engine="claude")
        cmd = mock_run.call_args[0][0]
        assert "--dangerously-skip-permissions" not in cmd

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_dangerous_full_access_bypasspermissions(self, mock_run: MagicMock):
        """DANGEROUS_FULL_ACCESS では --permission-mode bypassPermissions が付与される"""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("test prompt", engine="claude", capabilities=DANGEROUS_FULL_ACCESS)
        cmd = mock_run.call_args[0][0]
        assert "--permission-mode" in cmd
        perm_idx = cmd.index("--permission-mode")
        assert cmd[perm_idx + 1] == "bypassPermissions"


class TestCallCapabilities:
    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_default_uses_text_only(self, mock_run):
        """call() のデフォルトは TEXT_ONLY"""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("hello")
        cmd = mock_run.call_args[0][0]
        assert "--permission-mode" in cmd
        assert "--disallowed-tools" in cmd

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_dangerously_skip_permissions_true_claude(self, mock_run: MagicMock):
        """dangerously_skip_permissions=True + engine=claude → コマンドにフラグが付く"""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("hello", engine="claude", dangerously_skip_permissions=True)
        cmd = mock_run.call_args[0][0]
        assert "--dangerously-skip-permissions" in cmd

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_dangerously_skip_permissions_false_no_flag(self, mock_run: MagicMock):
        """dangerously_skip_permissions=False（デフォルト）→ フラグが付かない"""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("hello", engine="claude", dangerously_skip_permissions=False)
        cmd = mock_run.call_args[0][0]
        assert "--dangerously-skip-permissions" not in cmd

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_dangerously_skip_permissions_true_gemini_ignored(self, mock_run: MagicMock):
        """dangerously_skip_permissions=True + engine=gemini → Claude固有フラグは無視"""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("hello", engine="gemini", capabilities=LLMCapabilities(), dangerously_skip_permissions=True)
        cmd = mock_run.call_args[0][0]
        assert "--dangerously-skip-permissions" not in cmd

    def test_call_action_raises_type_error(self):
        """旧引数 action → TypeError"""
        with pytest.raises(TypeError):
            call("hello", action="skill")  # type: ignore

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_action_skill_cursor_force(self, mock_run: MagicMock):
        """cursor で dangerously_skip_permissions=True のとき --force が付与される"""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("test prompt", engine="cursor", dangerously_skip_permissions=True)
        cmd = mock_run.call_args[0][0]
        assert "--force" in cmd

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_with_json_only(self, mock_run):
        """JSON_ONLY capabilities が正しく渡される"""
        mock_run.return_value = MagicMock(stdout='{"k":"v"}', stderr="", returncode=0)
        result = call("hello", capabilities=JSON_ONLY)
        assert result.ok
        cmd = mock_run.call_args[0][0]
        assert "--output-format" in cmd

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_with_custom_capabilities(self, mock_run):
        """カスタム capabilities"""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        caps = LLMCapabilities(permission_mode="plan")
        call("hello", capabilities=caps)
        cmd = mock_run.call_args[0][0]
        assert "--permission-mode" in cmd
        perm_idx = cmd.index("--permission-mode")
        assert cmd[perm_idx + 1] == "plan"


# ---------------------------------------------------------------------------
# CLI テスト
# ---------------------------------------------------------------------------

class TestCLI:
    def test_llm_list_engines(self):
        """ghdag llm --list-engines"""
        import contextlib
        from io import StringIO

        from ghdag.cli import main

        buf = StringIO()
        with contextlib.redirect_stdout(buf):
            main(["llm", "--list-engines"])
        output = buf.getvalue()
        assert "claude" in output
        assert "gemini" in output

    def test_llm_list_models(self):
        """ghdag llm --list-models --engine claude"""
        import contextlib
        from io import StringIO

        from ghdag.cli import main

        buf = StringIO()
        with contextlib.redirect_stdout(buf):
            main(["llm", "--list-models", "--engine", "claude"])
        output = buf.getvalue()
        assert "claude-opus-4-6" in output
        assert "claude-sonnet-4-6" in output

    def test_llm_list_models_unknown_engine(self):
        """ghdag llm --list-models --engine unknown → exit 1"""
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc_info:
            main(["llm", "--list-models", "--engine", "unknown"])
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# EngineSpec / ENGINE_SPECS
# ---------------------------------------------------------------------------

class TestEngineSpec:
    def test_engine_specs_has_all_engines(self):
        assert "claude" in ENGINE_SPECS
        assert "gemini" in ENGINE_SPECS
        assert "cursor" in ENGINE_SPECS
        assert "shell" in ENGINE_SPECS

    def test_engine_spec_frozen(self):
        import dataclasses
        spec = ENGINE_SPECS["claude"]
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            spec.name = "other"  # type: ignore

    def test_claude_spec_cli(self):
        assert ENGINE_SPECS["claude"].cli == "claude"

    def test_gemini_spec_cli(self):
        assert ENGINE_SPECS["gemini"].cli == "gemini"

    def test_cursor_spec_cli(self):
        assert ENGINE_SPECS["cursor"].cli == "agent"

    def test_shell_spec_cli(self):
        assert ENGINE_SPECS["shell"].cli == "bash"

    def test_engine_specs_exported_from_ghdag_llm(self):
        assert EngineSpec is not None
        assert ENGINE_SPECS is not None

    def test_cursor_danger_flag_position_after_prompt(self):
        assert ENGINE_SPECS["cursor"].danger_flag_position == "after_prompt"

    def test_claude_danger_flag_position_trailing(self):
        assert ENGINE_SPECS["claude"].danger_flag_position == "trailing"

    def test_gemini_model_flag_is_double_dash(self):
        assert ENGINE_SPECS["gemini"].model_flag == "--model"

    def test_gemini_no_danger_flag(self):
        assert ENGINE_SPECS["gemini"].danger_flag is None

    def test_shell_no_model_flag(self):
        assert ENGINE_SPECS["shell"].model_flag is None

    def test_shell_extra_args_pipefail(self):
        assert "-o" in ENGINE_SPECS["shell"].extra_args
        assert "pipefail" in ENGINE_SPECS["shell"].extra_args


# ---------------------------------------------------------------------------
# render_exec_command
# ---------------------------------------------------------------------------

class TestRenderExecCommand:
    def test_claude_with_model(self):
        spec = ENGINE_SPECS["claude"]
        cmd = render_exec_command(spec, order_path="queue/order.md", prompt="hello", model="claude-opus-4-6")
        assert cmd == "cat queue/order.md | claude -p 'hello' --model 'claude-opus-4-6' --output-format json --dangerously-skip-permissions"

    def test_claude_without_model(self):
        spec = ENGINE_SPECS["claude"]
        cmd = render_exec_command(spec, order_path="queue/order.md", prompt="hello", model=None)
        assert cmd == "cat queue/order.md | claude -p 'hello' --output-format json --dangerously-skip-permissions"

    def test_gemini_with_model(self):
        spec = ENGINE_SPECS["gemini"]
        cmd = render_exec_command(spec, order_path="queue/order.md", prompt="hello", model="gemini-2.5-flash")
        assert cmd == "cat queue/order.md | gemini -p 'hello' --model 'gemini-2.5-flash' --approval-mode yolo"

    def test_gemini_no_dash_m(self):
        """gemini コマンドに -m を含まないこと（--model に統一）"""
        spec = ENGINE_SPECS["gemini"]
        cmd = render_exec_command(spec, order_path="queue/order.md", prompt="hello", model="gemini-2.5-flash")
        assert " -m " not in cmd
        assert "--model" in cmd

    def test_cursor_with_model(self):
        spec = ENGINE_SPECS["cursor"]
        cmd = render_exec_command(spec, order_path="queue/order.md", prompt="", model="auto")
        assert cmd == "agent --model 'auto' -p --force < queue/order.md"

    def test_cursor_without_model(self):
        spec = ENGINE_SPECS["cursor"]
        cmd = render_exec_command(spec, order_path="queue/order.md", prompt="", model=None)
        assert cmd == "agent -p --force < queue/order.md"

    def test_cursor_p_force_adjacent(self):
        """-p と --force が隣接していること"""
        spec = ENGINE_SPECS["cursor"]
        cmd = render_exec_command(spec, order_path="queue/order.md", prompt="", model="auto")
        assert "-p --force" in cmd

    def test_shell_command(self):
        spec = ENGINE_SPECS["shell"]
        cmd = render_exec_command(spec, order_path="queue/order.sh", prompt="", model=None)
        assert cmd == "bash -o pipefail queue/order.sh"


# ---------------------------------------------------------------------------
# Adapter outputs via spec
# ---------------------------------------------------------------------------

class TestAdapterOutputs:
    def test_claude_adapter_build_exec_record(self):
        adapter = get_adapter("claude")
        record = adapter.build_exec_record(
            uuid="u1",
            order_path="queue/order.md",
            result_path="results/result.md",
            prompt="hello",
            model="claude-sonnet-4-6",
            depends=[],
        )
        assert record["uuid"] == "u1"
        cmd = record["command"]
        assert "cat queue/order.md | claude -p 'hello' --model 'claude-sonnet-4-6' --output-format json --dangerously-skip-permissions" == cmd

    def test_gemini_adapter_uses_double_dash_model(self):
        """get_adapter('gemini') が -m ではなく --model を使う"""
        adapter = get_adapter("gemini")
        record = adapter.build_exec_record(
            uuid="u1",
            order_path="queue/order.md",
            result_path="results/result.md",
            prompt="hello",
            model="gemini-2.5-flash",
            depends=[],
        )
        cmd = record["command"]
        assert " -m " not in cmd
        assert "--model 'gemini-2.5-flash'" in cmd

    def test_cursor_adapter_p_force_adjacent(self):
        """get_adapter('cursor') の出力で -p --force が隣接"""
        adapter = get_adapter("cursor")
        record = adapter.build_exec_record(
            uuid="u1",
            order_path="queue/order.md",
            result_path="results/result.md",
            prompt="",
            model="auto",
            depends=[],
        )
        assert "-p --force" in record["command"]

    def test_shell_adapter_bash_pipefail(self):
        adapter = get_adapter("shell")
        record = adapter.build_exec_record(
            uuid="u1",
            order_path="queue/order.sh",
            result_path="results/result.md",
            prompt="",
            model=None,
            depends=[],
        )
        assert record["command"] == "bash -o pipefail queue/order.sh"


# ---------------------------------------------------------------------------
# render_exec_command with capabilities (AC1, AC2, AC3, AC8)
# ---------------------------------------------------------------------------

class TestRenderExecCommandCapabilities:
    def test_ac1_claude_text_only_capabilities(self):
        """AC1: capabilities=TEXT_ONLY → --permission-mode default --disallowed-tools ...、--dangerously-skip-permissions なし"""
        from ghdag.llm.capabilities import TEXT_ONLY
        spec = ENGINE_SPECS["claude"]
        cmd = render_exec_command(
            spec, order_path="queue/order.md", prompt="hello", model="claude-opus-4-6",
            capabilities=TEXT_ONLY,
        )
        assert "--permission-mode" in cmd
        assert "default" in cmd
        assert "--disallowed-tools" in cmd
        for tool in ("Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch"):
            assert tool in cmd
        assert "--dangerously-skip-permissions" not in cmd

    def test_ac2_claude_dangerous_full_access(self):
        """AC2: capabilities=DANGEROUS_FULL_ACCESS → --permission-mode bypassPermissions"""
        from ghdag.llm.capabilities import DANGEROUS_FULL_ACCESS
        spec = ENGINE_SPECS["claude"]
        cmd = render_exec_command(
            spec, order_path="queue/order.md", prompt="hello", model=None,
            capabilities=DANGEROUS_FULL_ACCESS,
        )
        assert "--permission-mode" in cmd
        assert "bypassPermissions" in cmd
        assert "--dangerously-skip-permissions" not in cmd

    def test_ac3_cursor_text_only_no_force(self):
        """AC3: cursor + capabilities=TEXT_ONLY → --force を含まない"""
        from ghdag.llm.capabilities import TEXT_ONLY
        spec = ENGINE_SPECS["cursor"]
        cmd = render_exec_command(
            spec, order_path="queue/order.md", prompt="", model="auto",
            capabilities=TEXT_ONLY,
        )
        assert "--force" not in cmd

    def test_ac8_claude_capabilities_none_preserves_danger_flag(self):
        """AC8: capabilities=None（デフォルト）→ 従来通り --dangerously-skip-permissions"""
        spec = ENGINE_SPECS["claude"]
        cmd = render_exec_command(
            spec, order_path="queue/order.md", prompt="hello", model="claude-opus-4-6",
            capabilities=None,
        )
        assert "--dangerously-skip-permissions" in cmd
        assert "--permission-mode" not in cmd

    def test_gemini_capabilities_fallback_to_no_flags(self):
        """gemini + capabilities → フラグなしにフォールバック（extra_args は維持）"""
        from ghdag.llm.capabilities import TEXT_ONLY
        spec = ENGINE_SPECS["gemini"]
        cmd = render_exec_command(
            spec, order_path="queue/order.md", prompt="hello", model="gemini-2.5-flash",
            capabilities=TEXT_ONLY,
        )
        assert "--permission-mode" not in cmd
        assert "--approval-mode yolo" in cmd  # extra_args は維持

    def test_shell_capabilities_no_change(self):
        """shell + capabilities → コマンド変化なし"""
        from ghdag.llm.capabilities import TEXT_ONLY
        spec = ENGINE_SPECS["shell"]
        cmd_no_caps = render_exec_command(spec, order_path="queue/order.sh", prompt="", model=None)
        cmd_with_caps = render_exec_command(spec, order_path="queue/order.sh", prompt="", model=None, capabilities=TEXT_ONLY)
        assert cmd_no_caps == cmd_with_caps
