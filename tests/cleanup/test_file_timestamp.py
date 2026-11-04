"""Tests for ghdag.cleanup — AC1–AC10 and Issue-856 fix coverage."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
from ghdag.cleanup import file_timestamp


class TestFileTimestamp:
    def test_returns_float(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("x")
        ts = file_timestamp(f)
        assert isinstance(ts, float)

    def test_prefers_birthtime_when_available(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("x")
        fake_stat = type("Stat", (), {"st_birthtime": 1_000_000.0, "st_mtime": 2_000_000.0})()
        with patch.object(Path, "stat", return_value=fake_stat):
            ts = file_timestamp(f)
        assert ts == 1_000_000.0

    def test_fallbacks_to_mtime_when_no_birthtime(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("x")
        fake_stat = type("Stat", (), {"st_mtime": 2_000_000.0})()
        with patch.object(Path, "stat", return_value=fake_stat):
            ts = file_timestamp(f)
        assert ts == 2_000_000.0
