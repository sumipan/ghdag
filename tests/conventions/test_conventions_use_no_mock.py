"""Convention test: tests/conventions/ must not import unittest.mock or mock."""

from __future__ import annotations

import ast
from pathlib import Path


def mock_imports(source: str) -> list[int]:
    """Return line numbers of mock-related import statements in source."""
    tree = ast.parse(source)
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in ("mock", "unittest.mock"):
                    lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in ("mock", "unittest.mock") or module.startswith("unittest.mock."):
                lines.append(node.lineno)
            elif module == "unittest":
                for alias in node.names:
                    if alias.name == "mock":
                        lines.append(node.lineno)
    return sorted(set(lines))


def test_conventions_files_have_no_mock_imports() -> None:
    conventions_dir = Path(__file__).resolve().parent
    violations: dict[str, list[int]] = {}
    for path in sorted(conventions_dir.glob("*.py")):
        lines = mock_imports(path.read_text(encoding="utf-8"))
        if lines:
            violations[path.name] = lines
    assert violations == {}, f"mock imports found: {violations}"


def test_mock_imports_detects_unittest_mock() -> None:
    fixture = "from unittest.mock import MagicMock\nx = 1\n"
    assert mock_imports(fixture) == [1]


def test_mock_imports_clean_source() -> None:
    source = "import os\nfrom pathlib import Path\n"
    assert mock_imports(source) == []


def test_mock_imports_detects_bare_mock_import() -> None:
    fixture = "import mock\n"
    assert mock_imports(fixture) == [1]
