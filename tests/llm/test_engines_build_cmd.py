"""Tests for ghdag.llm — one-shot LLM call interface"""

from __future__ import annotations

from ghdag.llm import (
    DANGEROUS_FULL_ACCESS,
    JSON_ONLY,
    TEXT_ONLY,
    WEB_RESEARCH,
    LLMCapabilities,
    build_llm_cmd,
)


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
        """Basic cursor command construction (CLI is cursor-agent, no capabilities flags)"""
        cmd = build_llm_cmd("cursor", "composer-2", "hello")
        assert cmd == ["cursor-agent", "--model", "composer-2", "-p"]
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

    def test_claude_web_search_merges_explicit_allowed_tools(self):
        caps = LLMCapabilities(
            web_search=True,
            allowed_tools=("Read", "WebSearch"),
        )
        cmd = build_llm_cmd("claude", "claude-sonnet-4-6", "hello", capabilities=caps)
        allowed_idx = cmd.index("--allowed-tools")
        assert cmd[allowed_idx + 1] == "Read,WebSearch,WebFetch"

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
