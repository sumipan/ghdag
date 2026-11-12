"""Tests for ghdag.llm.json_extract — JSON text extraction from LLM output."""

from __future__ import annotations

import pytest

from ghdag.core.exceptions import LLMParseError
from ghdag.llm.json_extract import extract_json, extract_json_text


class TestExtractJsonText:
    def test_prefers_last_valid_fence(self):
        text = 'Sure, here it is:\n```json\n{"a": 1}\n```\nDone.'
        assert extract_json_text(text) == '{"a": 1}'

    def test_ignores_braces_in_prose_and_string_literals(self):
        text = 'note {not json} first\n{"a": {"b": "}"}}'
        assert extract_json_text(text) == '{"a": {"b": "}"}}'

    def test_parses_top_level_array(self):
        assert extract_json_text("[1, 2]") == "[1, 2]"

    def test_empty_raises_with_empty_reason(self):
        with pytest.raises(LLMParseError) as exc_info:
            extract_json_text("")
        assert exc_info.value.reason == "empty"

    def test_no_json_raises_with_fence_hint(self):
        with pytest.raises(LLMParseError) as exc_info:
            extract_json_text("no json here")
        assert exc_info.value.code == "LLM_OUTPUT_NOT_JSON"
        assert "code fence" in (exc_info.value.hint or "").lower()

    def test_extract_json_parses_object(self):
        assert extract_json('{"x": true}') == {"x": True}

