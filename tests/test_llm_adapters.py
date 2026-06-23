"""Tests for ghdag.llm.adapters — EngineOutputAdapter レイヤの単体テスト (Issue #2266)."""

from __future__ import annotations

import json

import pytest

from ghdag.llm.adapters import get_output_adapter
from ghdag.llm.adapters.claude_json import ClaudeJsonAdapter
from ghdag.llm.adapters.claude_text import ClaudeTextAdapter
from ghdag.llm.adapters.cursor import CursorAdapter
from ghdag.metrics.models import TokenUsage

# ---------------------------------------------------------------------------
# ClaudeJsonAdapter
# ---------------------------------------------------------------------------

class TestClaudeJsonAdapterExtractResultText:
    def test_valid_json_extracts_result_field(self):
        """正常な JSON stdout → result フィールドの UTF-8 bytes が返る。"""
        payload = {"result": "テキスト本文", "usage": {"input_tokens": 10, "output_tokens": 5}}
        adapter = ClaudeJsonAdapter()
        out = adapter.extract_result_text(json.dumps(payload).encode(), b"")
        assert out == "テキスト本文".encode("utf-8")

    def test_valid_json_empty_result(self):
        """result フィールドが空文字列 → 空 bytes。"""
        payload = {"result": "", "usage": {}}
        adapter = ClaudeJsonAdapter()
        out = adapter.extract_result_text(json.dumps(payload).encode(), b"")
        assert out == b""

    def test_invalid_json_returns_raw_stdout(self):
        """不正な JSON stdout → raw stdout をそのまま返す（フォールバック）。"""
        raw = b"not json output at all"
        adapter = ClaudeJsonAdapter()
        out = adapter.extract_result_text(raw, b"")
        assert out == raw

    def test_empty_stdout_returns_empty(self):
        """空の stdout → 空 bytes。"""
        adapter = ClaudeJsonAdapter()
        out = adapter.extract_result_text(b"", b"")
        assert out == b""

    def test_non_utf8_invalid_returns_raw(self):
        """デコード不能な bytes → raw stdout をそのまま返す。"""
        raw = b"\xff\xfe invalid bytes"
        adapter = ClaudeJsonAdapter()
        out = adapter.extract_result_text(raw, b"")
        assert out == raw


class TestClaudeJsonAdapterExtractTokenUsage:
    def test_valid_json_extracts_full_usage(self):
        """正常な JSON stdout → TokenUsage が正しく抽出される。"""
        payload = {
            "result": "hello",
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "total_cost_usd": 0.0012,
            "cache_read_input_tokens": 30,
            "cache_creation_input_tokens": 10,
        }
        adapter = ClaudeJsonAdapter()
        usage = adapter.extract_token_usage(json.dumps(payload).encode(), b"")
        assert usage is not None
        assert usage.token_count == 150
        assert usage.cost_usd == pytest.approx(0.0012)
        assert usage.cache_read_tokens == 30
        assert usage.cache_creation_tokens == 10

    def test_valid_json_token_count_sum(self):
        """input_tokens + output_tokens が token_count になる。"""
        payload = {"result": "x", "usage": {"input_tokens": 200, "output_tokens": 75}}
        adapter = ClaudeJsonAdapter()
        usage = adapter.extract_token_usage(json.dumps(payload).encode(), b"")
        assert usage is not None
        assert usage.token_count == 275

    def test_valid_json_missing_cost(self):
        """total_cost_usd が無い場合 → cost_usd は None。"""
        payload = {"result": "x", "usage": {"input_tokens": 10, "output_tokens": 5}}
        adapter = ClaudeJsonAdapter()
        usage = adapter.extract_token_usage(json.dumps(payload).encode(), b"")
        assert usage is not None
        assert usage.token_count == 15
        assert usage.cost_usd is None
        assert usage.cache_read_tokens is None
        assert usage.cache_creation_tokens is None

    def test_invalid_json_returns_none(self):
        """不正な JSON stdout → TokenUsage は None（メトリクス欠損許容）。"""
        adapter = ClaudeJsonAdapter()
        usage = adapter.extract_token_usage(b"not json", b"")
        assert usage is None

    def test_empty_stdout_returns_none(self):
        """空 stdout → None。"""
        adapter = ClaudeJsonAdapter()
        usage = adapter.extract_token_usage(b"", b"")
        assert usage is None

    def test_zero_tokens_returns_none_token_count(self):
        """input + output が 0 → token_count は None（記録なし扱い）。"""
        payload = {"result": "x", "usage": {"input_tokens": 0, "output_tokens": 0}}
        adapter = ClaudeJsonAdapter()
        usage = adapter.extract_token_usage(json.dumps(payload).encode(), b"")
        assert usage is not None
        assert usage.token_count is None


# ---------------------------------------------------------------------------
# ClaudeTextAdapter
# ---------------------------------------------------------------------------

class TestClaudeTextAdapterExtractResultText:
    def test_returns_stdout_as_is(self):
        """stdout をそのまま返す（既存挙動温存）。"""
        raw = b"some output text"
        adapter = ClaudeTextAdapter()
        out = adapter.extract_result_text(raw, b"some stderr")
        assert out == raw

    def test_empty_stdout(self):
        """空 stdout → 空 bytes。"""
        adapter = ClaudeTextAdapter()
        assert adapter.extract_result_text(b"", b"") == b""


class TestClaudeTextAdapterExtractTokenUsage:
    def test_extracts_token_count_from_stderr(self):
        """stderr の 'Total tokens: N' パターンから token_count を抽出する。"""
        stderr = b"something Total tokens: 1234 something"
        adapter = ClaudeTextAdapter()
        usage = adapter.extract_token_usage(b"stdout text", stderr)
        assert usage is not None
        assert usage.token_count == 1234
        assert usage.cost_usd is None

    def test_extracts_input_output_tokens_from_stderr(self):
        """stderr の input_tokens/output_tokens パターンから合計を抽出する。"""
        stderr = b'input_tokens: 100\noutput_tokens: 50'
        adapter = ClaudeTextAdapter()
        usage = adapter.extract_token_usage(b"", stderr)
        assert usage is not None
        assert usage.token_count == 150

    def test_no_token_info_in_stderr_returns_none(self):
        """stderr にトークン情報なし → None。"""
        adapter = ClaudeTextAdapter()
        usage = adapter.extract_token_usage(b"stdout", b"no token info here")
        assert usage is None

    def test_empty_stderr_returns_none(self):
        """空 stderr → None。"""
        adapter = ClaudeTextAdapter()
        usage = adapter.extract_token_usage(b"stdout", b"")
        assert usage is None


# ---------------------------------------------------------------------------
# CursorAdapter
# ---------------------------------------------------------------------------

class TestCursorAdapter:
    def test_extract_result_text_passthrough(self):
        """stdout をそのまま返す（no-op）。"""
        raw = b"cursor output"
        adapter = CursorAdapter()
        out = adapter.extract_result_text(raw, b"stderr")
        assert out == raw

    def test_extract_token_usage_returns_none(self):
        """token_count / cost_usd は常に None（cursor は usage 未対応）。"""
        adapter = CursorAdapter()
        usage = adapter.extract_token_usage(b"stdout", b"stderr")
        assert usage is None


# ---------------------------------------------------------------------------
# get_output_adapter レジストリ
# ---------------------------------------------------------------------------

class TestGetOutputAdapter:
    def test_claude_returns_claude_json_adapter(self):
        adapter = get_output_adapter("claude")
        assert isinstance(adapter, ClaudeJsonAdapter)

    def test_cursor_returns_cursor_adapter(self):
        adapter = get_output_adapter("cursor")
        assert isinstance(adapter, CursorAdapter)

    def test_none_engine_returns_passthrough(self):
        """engine=None はデフォルトアダプターを返す（stdout パススルー）。"""
        adapter = get_output_adapter(None)
        raw = b"some output"
        assert adapter.extract_result_text(raw, b"") == raw
        assert adapter.extract_token_usage(raw, b"") is None

    def test_unknown_engine_returns_passthrough(self):
        """未知エンジン → デフォルトアダプター。"""
        adapter = get_output_adapter("gemini")
        raw = b"gemini output"
        assert adapter.extract_result_text(raw, b"") == raw
        assert adapter.extract_token_usage(raw, b"") is None


# ---------------------------------------------------------------------------
# TokenUsage dataclass
# ---------------------------------------------------------------------------

class TestTokenUsage:
    def test_all_none_by_default(self):
        u = TokenUsage()
        assert u.token_count is None
        assert u.cost_usd is None
        assert u.cache_read_tokens is None
        assert u.cache_creation_tokens is None

    def test_frozen(self):
        import dataclasses
        u = TokenUsage(token_count=100)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            u.token_count = 200  # type: ignore[misc]
