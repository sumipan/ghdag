"""Tests for ghdag.tool.schema — ToolDef and FallbackEntry."""

import pytest

from ghdag.tool.schema import TOOL_EXIT_CODES, FallbackEntry, ToolDef


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

    def test_default_exit_codes(self) -> None:
        tool = ToolDef(name="x", engine="claude-code", model="claude-opus-4-7")
        assert tool.exit_codes == ["success", "failure"]

    def test_valid_exit_codes(self) -> None:
        tool = ToolDef(
            name="x",
            engine="e",
            model="m",
            exit_codes=["success", "failure"],
        )
        assert tool.exit_codes == ["success", "failure"]

    def test_invalid_exit_code_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid exit_code"):
            ToolDef(name="x", engine="e", model="m", exit_codes=["invalid_code"])

    def test_empty_exit_codes_raises(self) -> None:
        with pytest.raises(ValueError, match="exit_codes must not be empty"):
            ToolDef(name="x", engine="e", model="m", exit_codes=[])

    def test_fallback_empty_by_default(self) -> None:
        tool = ToolDef(name="x", engine="e", model="m")
        assert tool.fallback == []


class TestToolExitCodes:
    def test_contains_phase_d_vocabulary(self) -> None:
        assert TOOL_EXIT_CODES == frozenset({"success", "failure", "retry", "skip"})


class TestFallbackEntry:
    def test_valid_instantiation(self) -> None:
        entry = FallbackEntry(engine="claude-code", model="claude-sonnet-4-6")
        assert entry.engine == "claude-code"
        assert entry.model == "claude-sonnet-4-6"
