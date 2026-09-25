"""Tests for ghdag.llm — one-shot LLM call interface"""

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
# Whitelist / validation
# ---------------------------------------------------------------------------

class TestEngineModels:
    def test_list_engines(self):
        """Returns the list of available engines"""
        engines = list_engines()
        assert "claude" in engines
        assert "gemini" in engines
        assert "cursor" in engines
        assert engines == sorted(engines)

    def test_list_models_claude(self):
        """Allowed models for the claude engine"""
        models = list_models("claude")
        assert "claude-sonnet-4-6" in models
        assert "claude-opus-4-6" in models
        assert models == sorted(models)

    def test_list_models_gemini(self, monkeypatch):
        """Default allowed models for gemini (excluding GHDAG_LLM_MODELS env influence)"""
        import ghdag.llm.engines as engines_mod
        from ghdag.llm._constants import DEFAULT_ENGINE_MODELS
        monkeypatch.setattr(engines_mod, "_ENGINE_MODELS", DEFAULT_ENGINE_MODELS)
        models = list_models("gemini")
        assert "gemini-2.5-flash" in models
        assert "gemini-2.5-pro" in models

    def test_list_models_cursor(self):
        """Allowed models for the cursor engine"""
        models = list_models("cursor")
        assert "auto" in models
        assert "composer-2" in models

    def test_list_models_unknown_engine(self):
        """Unknown engine → EngineModelError"""
        with pytest.raises(EngineModelError, match="Unknown engine"):
            list_models("openai")


class TestValidateEngineModel:
    def test_valid_claude_model(self):
        """claude + allowed model → returned as-is"""
        result = validate_engine_model("claude", "claude-opus-4-6")
        assert result == "claude-opus-4-6"

    def test_valid_gemini_model(self, monkeypatch):
        """gemini + allowed model → returned as-is (excluding GHDAG_LLM_MODELS env)"""
        import ghdag.llm.engines as engines_mod
        from ghdag.llm._constants import DEFAULT_ENGINE_MODELS
        monkeypatch.setattr(engines_mod, "_ENGINE_MODELS", DEFAULT_ENGINE_MODELS)
        result = validate_engine_model("gemini", "gemini-2.5-pro")
        assert result == "gemini-2.5-pro"

    def test_default_model_claude(self):
        """model=None → engine default"""
        result = validate_engine_model("claude", None)
        assert result == ENGINE_DEFAULTS["claude"]

    def test_default_model_gemini(self):
        """model=None → engine default"""
        result = validate_engine_model("gemini", None)
        assert result == ENGINE_DEFAULTS["gemini"]

    def test_valid_cursor_model(self):
        """cursor + allowed model → returned as-is"""
        result = validate_engine_model("cursor", "composer-2")
        assert result == "composer-2"

    def test_default_model_cursor(self):
        """cursor + model=None → defaults to \"auto\""""
        result = validate_engine_model("cursor", None)
        assert result == ENGINE_DEFAULTS["cursor"]
        assert result == "auto"

    def test_unknown_engine(self):
        """Unknown engine → EngineModelError"""
        with pytest.raises(EngineModelError, match="Unknown engine"):
            validate_engine_model("openai", "gpt-4o")

    def test_invalid_model(self):
        """Disallowed model → EngineModelError"""
        with pytest.raises(EngineModelError, match="Model not in allowlist"):
            validate_engine_model("claude", "gpt-4o")

    def test_invalid_model_contains_info(self):
        """Error message includes engine name and model name"""
        with pytest.raises(EngineModelError) as exc_info:
            validate_engine_model("claude", "unknown-model")
        msg = str(exc_info.value)
        assert "claude" in msg
        assert "unknown-model" in msg


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------

class TestBuildLLMCmd:
    def test_basic_claude(self):
        """Basic claude command construction (includes capabilities flags)"""
        cmd = build_llm_cmd("claude", "claude-opus-4-6", "hello")
        # Check basic elements
        assert cmd[0] == "claude"
        assert "--model" in cmd
        assert "claude-opus-4-6" in cmd
        assert "-p" in cmd
        # nexus #3805: STDIN engines keep the prompt body out of argv
        assert "hello" not in cmd
        # TEXT_ONLY default: permission-mode and disallowed-tools are attached
        assert "--permission-mode" in cmd
        assert "--disallowed-tools" in cmd

    def test_basic_gemini(self):
        """Basic gemini command construction (no capabilities flags)"""
        cmd = build_llm_cmd("gemini", "gemini-2.5-flash", "hello")
        assert cmd == ["gemini", "--model", "gemini-2.5-flash", "-p"]

    def test_basic_cursor(self):
        """Basic cursor command construction (CLI is agent, no capabilities flags)"""
        cmd = build_llm_cmd("cursor", "composer-2", "hello")
        assert cmd == ["agent", "--model", "composer-2", "-p"]


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
        """cursor with dangerously_skip_permissions=True attaches --force"""
        cmd = build_llm_cmd(
            "cursor", "auto", "hello",
            dangerously_skip_permissions=True,
        )
        assert "--force" in cmd

    def test_force_cursor_not_added_by_default(self):
        """cursor with dangerously_skip_permissions=False does not attach --force"""
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
        """JSON_ONLY + valid JSON → no LLMParseError"""
        r = LLMResult(stdout='{"k": "v"}', stderr="", returncode=0)
        result = r.validate(JSON_ONLY)
        assert result is r  # returns self

    def test_json_ok_invalid_json(self):
        """JSON_ONLY + non-JSON → LLMParseError"""
        r = LLMResult(stdout="not json", stderr="", returncode=0)
        with pytest.raises(LLMParseError) as exc_info:
            r.validate(JSON_ONLY)
        assert exc_info.value.raw == "not json"

    def test_json_fail_skips_validation(self):
        """returncode != 0 → skip validation"""
        r = LLMResult(stdout="err", stderr="", returncode=1)
        result = r.validate(JSON_ONLY)  # should not raise
        assert result is r

    def test_text_ok_no_validation(self):
        """TEXT_ONLY → no JSON validation"""
        r = LLMResult(stdout="not json", stderr="", returncode=0)
        result = r.validate(TEXT_ONLY)  # should not raise
        assert result is r

    def test_stream_extracts_result_from_jsonl(self):
        """stream=True → extract final result from JSONL"""
        jsonl = (
            '{"type":"system","subtype":"init"}\n'
            '{"type":"assistant","message":{"content":[{"type":"text","text":"partial"}]}}\n'
            '{"type":"result","subtype":"success","result":"final answer"}\n'
        )
        r = LLMResult(stdout=jsonl, stderr="", returncode=0)
        result = r.validate(LLMCapabilities(stream=True))
        assert result.stdout == "final answer"

    def test_stream_json_output_validated_after_extraction(self):
        """stream=True + output_format=json → JSON validate after extract"""
        jsonl = (
            '{"type":"result","subtype":"success","result":"{\\"k\\": \\"v\\"}"}\n'
        )
        r = LLMResult(stdout=jsonl, stderr="", returncode=0)
        result = r.validate(LLMCapabilities(stream=True, output_format="json"))
        assert result.stdout == '{"k": "v"}'

    def test_stream_no_result_line_raises(self):
        """stream JSONL without a result line → LLMParseError"""
        r = LLMResult(stdout='{"type":"system"}\n', stderr="", returncode=0)
        with pytest.raises(LLMParseError, match="no result line"):
            r.validate(LLMCapabilities(stream=True))


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
        """Custom permission_mode"""
        caps = LLMCapabilities(permission_mode="plan")
        cmd = build_llm_cmd("claude", "claude-sonnet-4-6", "hello", capabilities=caps)
        assert "--permission-mode" in cmd
        perm_idx = cmd.index("--permission-mode")
        assert cmd[perm_idx + 1] == "plan"

    def test_gemini_ignores_capabilities_flags(self):
        """gemini does not attach capabilities flags"""
        caps = LLMCapabilities(permission_mode="default", output_format="text")
        cmd = build_llm_cmd("gemini", "gemini-2.5-flash", "hello", capabilities=caps)
        assert "--permission-mode" not in cmd
        assert "--disallowed-tools" not in cmd

    def test_stream_true_adds_stream_json(self):
        """stream=True → --output-format stream-json --verbose (overrides output_format)"""
        caps = LLMCapabilities(stream=True)
        cmd = build_llm_cmd("claude", "claude-sonnet-4-6", "hello", capabilities=caps)
        assert "--output-format" in cmd
        fmt_idx = cmd.index("--output-format")
        assert cmd[fmt_idx + 1] == "stream-json"
        assert "--verbose" in cmd

    def test_stream_true_overrides_json_output_format(self):
        """With stream=True, stream-json takes precedence over output_format=json"""
        caps = LLMCapabilities(output_format="json", stream=True)
        cmd = build_llm_cmd("claude", "claude-sonnet-4-6", "hello", capabilities=caps)
        fmt_idx = cmd.index("--output-format")
        assert cmd[fmt_idx + 1] == "stream-json"
        assert cmd.count("--output-format") == 1


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
        """cursor engine accepts TEXT_ONLY (with disallowed_tools)"""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        result = call("hello", engine="cursor", capabilities=TEXT_ONLY)
        assert result.ok

    @patch("ghdag.llm.engines.subprocess.run")
    def test_claude_with_text_only_ok(self, mock_run):
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        result = call("hello", engine="claude", capabilities=TEXT_ONLY)
        assert result.ok

    @patch("ghdag.llm.engines.subprocess.run")
    def test_gemini_with_stream_raises(self, mock_run):
        with pytest.raises(NotImplementedError, match="stream"):
            call("hello", engine="gemini", capabilities=LLMCapabilities(stream=True))

    @patch("ghdag.llm.engines.subprocess.run")
    def test_cursor_with_stream_builds_flags(self, mock_run):
        """cursor + stream=True does not raise NotImplementedError; attaches stream-json."""
        mock_run.return_value = MagicMock(
            stdout=(
                '{"type":"result","subtype":"success","is_error":false,'
                '"result":"pong","session_id":"sess-1"}\n'
            ),
            stderr="",
            returncode=0,
        )
        result = call("hello", engine="cursor", capabilities=LLMCapabilities(stream=True))
        assert result.ok
        assert result.stdout == "pong"
        cmd = mock_run.call_args[0][0]
        assert cmd[cmd.index("--output-format") + 1] == "stream-json"
        assert "--stream-partial-output" in cmd


# ---------------------------------------------------------------------------
# call() — mock subprocess
# ---------------------------------------------------------------------------

class TestCall:
    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_default_model(self, mock_run: MagicMock):
        """model=None → call with the default model"""
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
        """Explicit model selection"""
        mock_run.return_value = MagicMock(
            stdout="ok", stderr="", returncode=0,
        )
        result = call("hello", engine="claude", model="claude-opus-4-6")
        assert result.ok
        cmd = mock_run.call_args[0][0]
        assert "claude-opus-4-6" in cmd

    def test_call_invalid_model(self):
        """Disallowed model → EngineModelError (before subprocess call)"""
        with pytest.raises(EngineModelError):
            call("hello", engine="claude", model="bad-model")

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_with_stdin(self, mock_run: MagicMock):
        """stdin_text is joined after the prompt on stdin (nexus #3805)"""
        mock_run.return_value = MagicMock(
            stdout="ok", stderr="", returncode=0,
        )
        call("hello", engine="claude", stdin_text="input data")
        _, kwargs = mock_run.call_args
        assert kwargs["input"] == "hello\n\ninput data"

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_with_timeout(self, mock_run: MagicMock):
        """timeout is passed through"""
        mock_run.return_value = MagicMock(
            stdout="ok", stderr="", returncode=0,
        )
        call("hello", engine="claude", timeout=30)
        _, kwargs = mock_run.call_args
        assert kwargs["timeout"] == 30

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_action_none_default_no_dangerously(self, mock_run: MagicMock):
        """Default capabilities do not attach --dangerously-skip-permissions"""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("test prompt", engine="claude")
        cmd = mock_run.call_args[0][0]
        assert "--dangerously-skip-permissions" not in cmd

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_dangerous_full_access_bypasspermissions(self, mock_run: MagicMock):
        """DANGEROUS_FULL_ACCESS attaches --permission-mode bypassPermissions"""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("test prompt", engine="claude", capabilities=DANGEROUS_FULL_ACCESS)
        cmd = mock_run.call_args[0][0]
        assert "--permission-mode" in cmd
        perm_idx = cmd.index("--permission-mode")
        assert cmd[perm_idx + 1] == "bypassPermissions"


class TestCallCapabilities:
    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_default_uses_text_only(self, mock_run):
        """call() defaults to TEXT_ONLY"""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("hello")
        cmd = mock_run.call_args[0][0]
        assert "--permission-mode" in cmd
        assert "--disallowed-tools" in cmd

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_dangerously_skip_permissions_true_claude(self, mock_run: MagicMock):
        """dangerously_skip_permissions=True + engine=claude → flag on command"""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("hello", engine="claude", dangerously_skip_permissions=True)
        cmd = mock_run.call_args[0][0]
        assert "--dangerously-skip-permissions" in cmd

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_dangerously_skip_permissions_false_no_flag(self, mock_run: MagicMock):
        """dangerously_skip_permissions=False (default) → no flag"""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("hello", engine="claude", dangerously_skip_permissions=False)
        cmd = mock_run.call_args[0][0]
        assert "--dangerously-skip-permissions" not in cmd

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_dangerously_skip_permissions_true_gemini_ignored(self, mock_run: MagicMock):
        """dangerously_skip_permissions=True + engine=gemini → Claude-specific flag ignored"""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("hello", engine="gemini", capabilities=LLMCapabilities(), dangerously_skip_permissions=True)
        cmd = mock_run.call_args[0][0]
        assert "--dangerously-skip-permissions" not in cmd

    def test_call_action_raises_type_error(self):
        """Legacy action arg → TypeError"""
        with pytest.raises(TypeError):
            call("hello", action="skill")  # type: ignore

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_action_skill_cursor_force(self, mock_run: MagicMock):
        """cursor with dangerously_skip_permissions=True attaches --force"""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("test prompt", engine="cursor", dangerously_skip_permissions=True)
        cmd = mock_run.call_args[0][0]
        assert "--force" in cmd

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_with_json_only(self, mock_run):
        """JSON_ONLY capabilities are passed correctly"""
        mock_run.return_value = MagicMock(stdout='{"k":"v"}', stderr="", returncode=0)
        result = call("hello", capabilities=JSON_ONLY)
        assert result.ok
        cmd = mock_run.call_args[0][0]
        assert "--output-format" in cmd

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_with_custom_capabilities(self, mock_run):
        """Custom capabilities"""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        caps = LLMCapabilities(permission_mode="plan")
        call("hello", capabilities=caps)
        cmd = mock_run.call_args[0][0]
        assert "--permission-mode" in cmd
        perm_idx = cmd.index("--permission-mode")
        assert cmd[perm_idx + 1] == "plan"


# ---------------------------------------------------------------------------
# CLI tests
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

    def test_cursor_danger_flag_position_leading(self):
        assert ENGINE_SPECS["cursor"].danger_flag_position == "leading"

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
        cmd = render_exec_command(spec, order_path="queue/order.md", model="claude-opus-4-6")
        assert cmd == "claude -p --model 'claude-opus-4-6' --output-format stream-json --verbose --dangerously-skip-permissions < queue/order.md"

    def test_claude_without_model(self):
        spec = ENGINE_SPECS["claude"]
        cmd = render_exec_command(spec, order_path="queue/order.md", model=None)
        assert cmd == "claude -p --output-format stream-json --verbose --dangerously-skip-permissions < queue/order.md"

    def test_gemini_with_model(self):
        spec = ENGINE_SPECS["gemini"]
        cmd = render_exec_command(spec, order_path="queue/order.md", model="gemini-2.5-flash")
        assert cmd == "gemini -p --model 'gemini-2.5-flash' --approval-mode yolo < queue/order.md"

    def test_gemini_no_dash_m(self):
        """gemini command must not include -m (unified on --model)"""
        spec = ENGINE_SPECS["gemini"]
        cmd = render_exec_command(spec, order_path="queue/order.md", model="gemini-2.5-flash")
        assert " -m " not in cmd
        assert "--model" in cmd

    def test_cursor_with_model(self):
        spec = ENGINE_SPECS["cursor"]
        cmd = render_exec_command(spec, order_path="queue/order.md", model="auto")
        assert cmd == (
            "agent --model 'auto' -p --force --output-format stream-json "
            "--stream-partial-output < queue/order.md"
        )

    def test_cursor_without_model(self):
        spec = ENGINE_SPECS["cursor"]
        cmd = render_exec_command(spec, order_path="queue/order.md", model=None)
        assert cmd == (
            "agent -p --force --output-format stream-json "
            "--stream-partial-output < queue/order.md"
        )

    def test_cursor_p_force_adjacent(self):
        """-p and --force are adjacent"""
        spec = ENGINE_SPECS["cursor"]
        cmd = render_exec_command(spec, order_path="queue/order.md", model="auto")
        assert "-p --force" in cmd

    def test_cursor_includes_output_format_stream_json(self):
        """DAG-path cursor command includes --output-format stream-json."""
        spec = ENGINE_SPECS["cursor"]
        cmd = render_exec_command(spec, order_path="queue/order.md", model="auto")
        assert "--output-format stream-json" in cmd
        assert "--stream-partial-output" in cmd

    def test_shell_command(self):
        spec = ENGINE_SPECS["shell"]
        cmd = render_exec_command(spec, order_path="queue/order.sh", model=None)
        assert cmd == "bash -o pipefail queue/order.sh"

    def test_prompt_ignored_for_flag_only(self):
        """FLAG_ONLY does not put the prompt string on argv"""
        spec = ENGINE_SPECS["claude"]
        cmd = render_exec_command(
            spec, order_path="jobs/o.md", model="claude-sonnet-4-6", prompt="any-string",
        )
        assert "-p '" not in cmd
        assert "any-string" not in cmd

    def test_no_engine_embeds_prompt_value_in_argv(self):
        """Generated commands for all ENGINE_SPECS must not contain `-p '`"""
        for name, spec in ENGINE_SPECS.items():
            cmd = render_exec_command(
                spec, order_path="jobs/o.md", model=spec.default_model, prompt="hello",
            )
            assert "-p '" not in cmd, f"{name}: {cmd}"


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
            model="claude-sonnet-4-6",
            depends=[],
        )
        assert record["uuid"] == "u1"
        cmd = record["command"]
        assert cmd == (
            "claude -p --model 'claude-sonnet-4-6' --output-format stream-json --verbose"
            " --dangerously-skip-permissions < queue/order.md"
        )

    def test_claude_adapter_prompt_optional(self):
        """build_exec_record can be called without prompt"""
        adapter = get_adapter("claude")
        record = adapter.build_exec_record(
            uuid="u1",
            order_path="queue/order.md",
            result_path="results/result.md",
            model="claude-sonnet-4-6",
            depends=[],
        )
        assert "-p '" not in record["command"]

    def test_gemini_adapter_uses_double_dash_model(self):
        """get_adapter('gemini') uses --model, not -m"""
        adapter = get_adapter("gemini")
        record = adapter.build_exec_record(
            uuid="u1",
            order_path="queue/order.md",
            result_path="results/result.md",
            model="gemini-2.5-flash",
            depends=[],
        )
        cmd = record["command"]
        assert " -m " not in cmd
        assert "--model 'gemini-2.5-flash'" in cmd

    def test_cursor_adapter_p_force_adjacent(self):
        """get_adapter('cursor') output has adjacent -p --force"""
        adapter = get_adapter("cursor")
        record = adapter.build_exec_record(
            uuid="u1",
            order_path="queue/order.md",
            result_path="results/result.md",
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
            model=None,
            depends=[],
        )
        assert record["command"] == "bash -o pipefail queue/order.sh"


# ---------------------------------------------------------------------------
# render_exec_command with capabilities (AC1, AC2, AC3, AC8)
# ---------------------------------------------------------------------------

class TestRenderExecCommandCapabilities:
    def test_ac1_claude_text_only_capabilities(self):
        """AC1: capabilities=TEXT_ONLY → --permission-mode default --disallowed-tools ...; no --dangerously-skip-permissions"""
        from ghdag.llm.capabilities import TEXT_ONLY
        spec = ENGINE_SPECS["claude"]
        cmd = render_exec_command(
            spec, order_path="queue/order.md", model="claude-opus-4-6",
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
            spec, order_path="queue/order.md", model=None,
            capabilities=DANGEROUS_FULL_ACCESS,
        )
        assert "--permission-mode" in cmd
        assert "bypassPermissions" in cmd
        assert "--dangerously-skip-permissions" not in cmd

    def test_ac3_cursor_text_only_no_force(self):
        """AC3: cursor + capabilities=TEXT_ONLY → does not include --force"""
        from ghdag.llm.capabilities import TEXT_ONLY
        spec = ENGINE_SPECS["cursor"]
        cmd = render_exec_command(
            spec, order_path="queue/order.md", model="auto",
            capabilities=TEXT_ONLY,
        )
        assert "--force" not in cmd

    def test_ac8_claude_capabilities_none_preserves_danger_flag(self):
        """AC8: capabilities=None (default) → --dangerously-skip-permissions as before"""
        spec = ENGINE_SPECS["claude"]
        cmd = render_exec_command(
            spec, order_path="queue/order.md", model="claude-opus-4-6",
            capabilities=None,
        )
        assert "--dangerously-skip-permissions" in cmd
        assert "--permission-mode" not in cmd

    def test_gemini_capabilities_fallback_to_no_flags(self):
        """gemini + capabilities → fall back to no flags (extra_args kept)"""
        from ghdag.llm.capabilities import TEXT_ONLY
        spec = ENGINE_SPECS["gemini"]
        cmd = render_exec_command(
            spec, order_path="queue/order.md", model="gemini-2.5-flash",
            capabilities=TEXT_ONLY,
        )
        assert "--permission-mode" not in cmd
        assert "--approval-mode yolo" in cmd  # extra_args kept

    def test_shell_capabilities_no_change(self):
        """shell + capabilities → command unchanged"""
        from ghdag.llm.capabilities import TEXT_ONLY
        spec = ENGINE_SPECS["shell"]
        cmd_no_caps = render_exec_command(spec, order_path="queue/order.sh", model=None)
        cmd_with_caps = render_exec_command(spec, order_path="queue/order.sh", model=None, capabilities=TEXT_ONLY)
        assert cmd_no_caps == cmd_with_caps

    def test_claude_stream_capabilities(self):
        """stream=True → render_exec_command includes stream-json flags"""
        caps = LLMCapabilities(stream=True)
        spec = ENGINE_SPECS["claude"]
        cmd = render_exec_command(
            spec, order_path="queue/order.md", model="claude-opus-4-6",
            capabilities=caps,
        )
        assert "--output-format" in cmd
        assert "stream-json" in cmd
        assert "--verbose" in cmd

    def test_cursor_stream_prefers_stream_json_without_duplicate(self):
        """With cursor stream, stream-json wins and --output-format appears once."""
        caps = LLMCapabilities(stream=True)
        spec = ENGINE_SPECS["cursor"]
        cmd = render_exec_command(
            spec, order_path="queue/order.md", model="auto",
            capabilities=caps,
        )
        assert cmd.split().count("--output-format") == 1
        assert "--output-format stream-json" in cmd
        assert "--output-format json" not in cmd


class TestSupportsCapability:
    """supports_capability(engine, capability) — nexus #3034 AC-1 / CLAUDE.md §11."""

    @pytest.mark.parametrize(
        "engine,capability,expected",
        [
            ("claude", "resume", True),
            ("claude", "stream", True),
            ("claude", "output_format", True),
            ("cursor", "resume", True),
            ("cursor", "stream", True),
            ("cursor", "output_format", True),
            ("codex", "resume", True),
            ("codex", "stream", True),
            ("codex", "output_format", False),
            # nexus #3044 — isolation (global config isolation)
            ("claude", "isolation", True),
            ("codex", "isolation", True),
            ("cursor", "isolation", False),
        ],
    )
    def test_three_engines_core_capabilities(
        self, engine: str, capability: str, expected: bool
    ) -> None:
        from ghdag.llm.engines import supports_capability

        assert supports_capability(engine, capability) is expected

    def test_unknown_engine_and_capability_are_conservative_true(self) -> None:
        from ghdag.llm.engines import supports_capability

        assert supports_capability("unknown-engine", "resume") is True
        assert supports_capability("claude", "unknown_capability") is True

    def test_ignored_capability_not_counted_as_unsupported(self) -> None:
        """Attrs in _IGNORED are excluded from effective_unsupported and yield True."""
        from ghdag.llm.engines import supports_capability

        assert supports_capability("codex", "allowed_tools") is True
        assert supports_capability("cursor", "disallowed_tools") is True

    def test_cursor_unsupported_includes_isolation_with_reason(self) -> None:
        """_UNSUPPORTED_CAPABILITIES['cursor'] includes isolation (nexus #3044)."""
        from ghdag.llm.engines import _UNSUPPORTED_CAPABILITIES

        assert "isolation" in _UNSUPPORTED_CAPABILITIES["cursor"]


class TestEngineIsolation:
    """Engine isolation opt-in — nexus #3174 (conditionalizes always-on isolation from #3044)."""

    def test_claude_cmd_omits_disable_slash_commands_by_default(self) -> None:
        """By default do not attach --disable-slash-commands (skills remain usable)."""
        cmd = build_llm_cmd("claude", "claude-sonnet-4-6", "hello")
        assert "--disable-slash-commands" not in cmd
        assert "--disable-slash-commands" not in ENGINE_SPECS["claude"].extra_args

    def test_claude_cmd_includes_disable_slash_commands_when_isolation_true(
        self,
    ) -> None:
        cmd = build_llm_cmd("claude", "claude-sonnet-4-6", "hello", isolation=True)
        assert "--disable-slash-commands" in cmd

    @patch("ghdag.llm.engines.subprocess.run")
    def test_codex_call_sets_codex_home_when_isolation_and_auth(
        self, mock_run: MagicMock, tmp_path, monkeypatch
    ) -> None:
        """Inject CODEX_HOME only when isolation=True and auth.json exists."""
        import ghdag.llm.engines as engines_mod

        monkeypatch.setattr(engines_mod, "_CODEX_DAG_HOME", str(tmp_path) + "/")
        (tmp_path / "auth.json").write_text("{}", encoding="utf-8")
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("hello", engine="codex", capabilities=TEXT_ONLY, isolation=True)
        assert mock_run.called
        kwargs = mock_run.call_args.kwargs
        assert "env" in kwargs
        assert kwargs["env"]["CODEX_HOME"] == str(tmp_path) + "/"

    @patch("ghdag.llm.engines.subprocess.run")
    def test_codex_isolation_skipped_without_auth(
        self, mock_run: MagicMock, tmp_path, monkeypatch, capsys
    ) -> None:
        """With isolation=True but no auth.json, do not inject CODEX_HOME; warn."""
        import ghdag.llm.engines as engines_mod

        monkeypatch.setattr(engines_mod, "_CODEX_DAG_HOME", str(tmp_path) + "/")
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("hello", engine="codex", capabilities=TEXT_ONLY, isolation=True)
        kwargs = mock_run.call_args.kwargs
        assert "env" not in kwargs or "CODEX_HOME" not in kwargs.get("env", {})
        err = capsys.readouterr().err
        assert "codex isolation skipped" in err

    @patch("ghdag.llm.engines.subprocess.run")
    def test_codex_call_no_codex_home_by_default(
        self, mock_run: MagicMock, monkeypatch
    ) -> None:
        """With isolation=None and GHDAG_ENGINE_ISOLATION unset, do not inject CODEX_HOME."""
        monkeypatch.delenv("GHDAG_ENGINE_ISOLATION", raising=False)
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("hello", engine="codex", capabilities=TEXT_ONLY, isolation=None)
        kwargs = mock_run.call_args.kwargs
        assert "env" not in kwargs or "CODEX_HOME" not in kwargs.get("env", {})

    @patch("ghdag.llm.engines.subprocess.run")
    def test_cursor_call_unaffected_by_isolation_meta_capability(
        self, mock_run: MagicMock
    ) -> None:
        """isolation is not an LLMCapabilities attr, so cursor call stays intact."""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        result = call("hello", engine="cursor", capabilities=TEXT_ONLY)
        assert result.ok
