"""Tests for ghdag.tool.schema — ToolDef and FallbackEntry."""

import pytest

from ghdag.tool.schema import FallbackEntry, ToolDef


class TestToolDef:
    def test_valid_instantiation(self) -> None:
        tool = ToolDef(name="x", engine="claude-code", model="claude-opus-4-7")
        assert tool.name == "x"
        assert tool.engine == "claude-code"
        assert tool.model == "claude-opus-4-7"
        assert tool.fallback == []

    def test_empty_name_raises(self) -> None:
        with pytest.raises(ValueError, match="ToolDef.name must not be empty"):
            ToolDef(name="", engine="claude-code", model="claude-opus-4-7")

    def test_empty_engine_raises(self) -> None:
        with pytest.raises(ValueError, match="ToolDef.engine must not be empty"):
            ToolDef(name="x", engine="", model="claude-opus-4-7")

    def test_empty_model_raises(self) -> None:
        with pytest.raises(ValueError, match="ToolDef.model must not be empty"):
            ToolDef(name="x", engine="claude-code", model="")


class TestFallbackEntry:
    def test_valid_instantiation(self) -> None:
        entry = FallbackEntry(engine="claude-code", model="claude-sonnet-4-6")
        assert entry.engine == "claude-code"
        assert entry.model == "claude-sonnet-4-6"
