"""Tests for correlation burst detection helpers in pipeline/audit_query.py."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from ghdag.io.audit_query import detect_correlation_bursts, get_correlation_top_n

_FIXED_EPOCH = 1_700_000_000.0
_JST = timezone(timedelta(hours=9))


def _write_events(path: Path, events: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def _ts(offset_sec: float = 0.0) -> str:
    dt = datetime.fromtimestamp(_FIXED_EPOCH - offset_sec, tz=_JST)
    return dt.isoformat()


@pytest.fixture(autouse=True)
def _fixed_query_clock():
    with patch("ghdag.io.audit_query.time.time", return_value=_FIXED_EPOCH):
        yield


@pytest.fixture
def audit_path(tmp_path):
    return tmp_path / "audit.jsonl"


class TestDetectCorrelationBursts:

    def test_missing_file_returns_empty(self, tmp_path):
        result = detect_correlation_bursts(tmp_path / "nonexistent.jsonl")
        assert result == []


class TestGetCorrelationTopN:

    def test_missing_file_returns_empty(self, tmp_path):
        result = get_correlation_top_n(tmp_path / "nonexistent.jsonl", since_sec=3600)
        assert result == []
