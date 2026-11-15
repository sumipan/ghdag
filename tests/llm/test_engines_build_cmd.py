"""Tests for ghdag.llm — one-shot LLM call interface"""

from __future__ import annotations

from ghdag.core.capabilities import WEB_RESEARCH
from ghdag.llm import Permission
from ghdag.llm.engines import build_llm_cmd


class TestBuildLLMCmd:

    def test_basic_gemini(self):
        """Basic gemini command construction (no capabilities flags)"""
        cmd = build_llm_cmd("gemini", "gemini-2.5-flash", "hello")
        assert cmd == ["gemini", "--model", "gemini-2.5-flash", "-p"]

    def test_basic_cursor(self):
        """Basic cursor command construction (CLI is cursor-agent, no capabilities flags)"""
        cmd = build_llm_cmd("cursor", "composer-2", "hello")
        assert cmd == ["cursor-agent", "--model", "composer-2", "-p"]


class TestBuildLLMCmdCapabilities:

    def test_web_research(self):
        """WEB_RESEARCH: --allowed-tools and --disallowed-tools both present"""
        cmd = build_llm_cmd("claude", "claude-sonnet-4-6", "hello", permission=WEB_RESEARCH)
        assert "--allowed-tools" in cmd
        allowed_idx = cmd.index("--allowed-tools")
        allowed_val = cmd[allowed_idx + 1]
        for tool in ("WebFetch", "WebSearch", "Read", "Grep", "Glob"):
            assert tool in allowed_val
        assert "--disallowed-tools" in cmd

    def test_claude_web_search_merges_explicit_allowed_tools(self):
        caps = Permission(
            web_search=True,
            allowed_tools=("Read", "WebSearch"),
        )
        cmd = build_llm_cmd("claude", "claude-sonnet-4-6", "hello", permission=caps)
        allowed_idx = cmd.index("--allowed-tools")
        assert cmd[allowed_idx + 1] == "Read,WebSearch,WebFetch"
