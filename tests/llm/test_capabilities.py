"""Tests for ghdag.llm — one-shot LLM call interface"""

from __future__ import annotations

import pytest

from ghdag.llm import (
    DANGEROUS_FULL_ACCESS,
    JSON_ONLY,
    TEXT_ONLY,
    WEB_RESEARCH,
    LLMCapabilities,
    build_llm_cmd,
)


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
        assert WEB_RESEARCH.web_search is True
        assert WEB_RESEARCH.allowed_tools == ()
        assert "Bash" in WEB_RESEARCH.disallowed_tools
        assert "Edit" in WEB_RESEARCH.disallowed_tools

    def test_default_capabilities_web_search_false(self):
        assert LLMCapabilities().web_search is False

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


class TestLLMParseErrorCodes:
    def test_llm_parse_error_declares_code_and_exit(self):
        """#6142: LLMParseError uses LLM_OUTPUT_NOT_JSON and exit 3."""
        from ghdag.llm.capabilities import LLMParseError

        err = LLMParseError("not json", "invalid")
        assert err.code == "LLM_OUTPUT_NOT_JSON"
        assert err.exit_code == 3
        assert err.raw == "not json"
