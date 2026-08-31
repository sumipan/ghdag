"""core 純度ガード — IO / subprocess 等の混入を禁止する。"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_CORE_ROOT = Path(__file__).resolve().parents[2] / "src" / "ghdag" / "core"

_FORBIDDEN_MODULES = frozenset(
    {"subprocess", "urllib", "http", "socket", "requests"}
)


def _core_py_files() -> list[Path]:
    return sorted(_CORE_ROOT.rglob("*.py"))


def _module_root(name: str) -> str:
    return name.split(".", 1)[0]


@pytest.mark.parametrize("path", _core_py_files(), ids=lambda p: str(p.relative_to(_CORE_ROOT)))
def test_core_has_no_forbidden_io(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _module_root(alias.name) in _FORBIDDEN_MODULES:
                    violations.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module and _module_root(node.module) in _FORBIDDEN_MODULES:
                violations.append(f"from {node.module} import ...")
        elif isinstance(node, ast.Attribute):
            # os.environ
            if (
                isinstance(node.value, ast.Name)
                and node.value.id == "os"
                and node.attr == "environ"
            ):
                violations.append("os.environ")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "open":
                violations.append("open()")
            elif (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
                and func.attr == "environ"
            ):
                violations.append("os.environ(...)")

    assert not violations, f"{path.relative_to(_CORE_ROOT)}: {violations}"
