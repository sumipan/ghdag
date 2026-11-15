"""Tests for correlation burst detection helpers in pipeline/audit_query.py."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ghdag.io.audit_query import detect_correlation_bursts, get_correlation_top_n

_FIXED_EPOCH = 1_700_000_000.0


@pytest.fixture(autouse=True)
def _fixed_query_clock():
    with patch("ghdag.io.audit_query.time.time", return_value=_FIXED_EPOCH):
        yield


class TestDetectCorrelationBursts:

    def test_missing_file_returns_empty(self, tmp_path):
        result = detect_correlation_bursts(tmp_path / "nonexistent.jsonl")
        assert result == []


class TestGetCorrelationTopN:

    def test_missing_file_returns_empty(self, tmp_path):
        result = get_correlation_top_n(tmp_path / "nonexistent.jsonl", since_sec=3600)
        assert result == []
