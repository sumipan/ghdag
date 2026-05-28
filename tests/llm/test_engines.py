"""Tests for LLMResult.latency_ms and call() latency measurement — Issue #1275."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ghdag.llm.engines import LLMResult, call


class TestLLMResultLatencyMs:
    def test_llm_result_latency_ms_default(self):
        """latency_ms 未指定時のデフォルトは 0.0。"""
        r = LLMResult(stdout="out", stderr="", returncode=0)
        assert r.latency_ms == 0.0

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_measures_latency(self, mock_run: MagicMock):
        """call() は latency_ms > 0 を返す。"""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        result = call("hello", engine="claude")
        assert result.latency_ms > 0
