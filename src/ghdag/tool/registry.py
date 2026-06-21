"""ghdag.tool.registry — Tool discovery and registry."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from ghdag.tool.exceptions import ToolRegistryError
from ghdag.tool.schema import ToolDef

_FILENAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*\.py$")
_SKIP_FILES = frozenset({"__init__.py"})


class ToolRegistry:
    @staticmethod
    def discover(path: Path) -> dict[str, ToolDef]:
        """指定ディレクトリを walk し、.py ファイルから ToolDef を収集する。

        各 .py ファイルはモジュールレベルの `tool` 変数に ToolDef インスタンスを
        export する規約。importlib.util で動的インポートし tool 変数を取得する。

        Raises:
            FileNotFoundError: path が存在しない場合
            ToolRegistryError: ファイル名規約違反または同名 Tool の多重定義
        """
        if not path.exists():
            raise FileNotFoundError(f"Directory not found: {path}")

        tools: dict[str, ToolDef] = {}
        sources: dict[str, Path] = {}

        for py_file in sorted(path.glob("*.py")):
            if py_file.name in _SKIP_FILES:
                continue

            if not _FILENAME_PATTERN.match(py_file.name):
                raise ToolRegistryError(
                    f"Invalid tool filename (must match [a-z][a-z0-9_]*.py): {py_file.name}"
                )

            tool = _load_tool_from_file(py_file)

            if tool.name in tools:
                prev = sources[tool.name]
                raise ToolRegistryError(
                    f"Duplicate tool name '{tool.name}': {prev} and {py_file}"
                )

            tools[tool.name] = tool
            sources[tool.name] = py_file

        return tools


def _load_tool_from_file(py_file: Path) -> ToolDef:
    module_name = f"ghdag_tool_{py_file.stem}"
    spec = importlib.util.spec_from_file_location(module_name, py_file)
    if spec is None or spec.loader is None:
        raise ToolRegistryError(f"Failed to load module from {py_file}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    try:
        tool = module.tool
    except AttributeError as e:
        raise ToolRegistryError(
            f"Module {py_file.name} must export a 'tool' variable"
        ) from e

    if not isinstance(tool, ToolDef):
        raise ToolRegistryError(
            f"Module {py_file.name}: 'tool' must be a ToolDef instance, "
            f"got {type(tool).__name__}"
        )

    return tool
