"""Tests for ghdag.tool.registry — ToolRegistry.discover."""

from pathlib import Path

import pytest

from ghdag.exceptions import GhdagError
from ghdag.tool.exceptions import ToolRegistryError
from ghdag.tool.registry import ToolRegistry


def _write_tool(path: Path, name: str, *, filename: str | None = None) -> Path:
    fname = filename or f"{name}.py"
    tool_path = path / fname
    tool_path.write_text(
        f'''\
from ghdag.tool.schema import ToolDef

tool = ToolDef(name="{name}", engine="claude-code", model="claude-opus-4-7")
''',
        encoding="utf-8",
    )
    return tool_path


class TestToolRegistryError:
    def test_is_ghdag_error_subclass(self) -> None:
        assert issubclass(ToolRegistryError, GhdagError)


class TestDiscoverBasics:
    def test_empty_directory(self, tmp_path: Path) -> None:
        assert ToolRegistry.discover(tmp_path) == {}

    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            ToolRegistry.discover(tmp_path / "missing")

    def test_valid_tool(self, tmp_path: Path) -> None:
        _write_tool(tmp_path, "my_tool")
        result = ToolRegistry.discover(tmp_path)
        assert len(result) == 1
        assert "my_tool" in result
        assert result["my_tool"].engine == "claude-code"

    def test_init_py_excluded(self, tmp_path: Path) -> None:
        (tmp_path / "__init__.py").write_text(
            '''\
from ghdag.tool.schema import ToolDef
tool = ToolDef(name="init_tool", engine="claude-code", model="claude-opus-4-7")
''',
            encoding="utf-8",
        )
        assert ToolRegistry.discover(tmp_path) == {}


class TestFilenameConvention:
    def test_invalid_prefix_underscore(self, tmp_path: Path) -> None:
        _write_tool(tmp_path, "invalid", filename="_invalid.py")
        with pytest.raises(ToolRegistryError, match="Invalid tool filename"):
            ToolRegistry.discover(tmp_path)

    def test_invalid_starts_with_digit(self, tmp_path: Path) -> None:
        _write_tool(tmp_path, "start", filename="9start.py")
        with pytest.raises(ToolRegistryError, match="Invalid tool filename"):
            ToolRegistry.discover(tmp_path)


class TestToolExport:
    def test_missing_tool_variable(self, tmp_path: Path) -> None:
        (tmp_path / "notool.py").write_text("# no tool export\n", encoding="utf-8")
        with pytest.raises(ToolRegistryError, match="must export a 'tool' variable"):
            ToolRegistry.discover(tmp_path)

    def test_wrong_tool_type(self, tmp_path: Path) -> None:
        (tmp_path / "badtype.py").write_text('tool = "not a ToolDef"\n', encoding="utf-8")
        with pytest.raises(ToolRegistryError, match="must be a ToolDef instance"):
            ToolRegistry.discover(tmp_path)


class TestDuplicateDetection:
    def test_duplicate_tool_name(self, tmp_path: Path) -> None:
        _write_tool(tmp_path, "same_name", filename="tool_a.py")
        _write_tool(tmp_path, "same_name", filename="tool_b.py")
        with pytest.raises(ToolRegistryError, match="Duplicate tool name 'same_name'") as exc_info:
            ToolRegistry.discover(tmp_path)
        msg = str(exc_info.value)
        assert "tool_a.py" in msg
        assert "tool_b.py" in msg
