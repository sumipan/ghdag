"""Convention: TaskLauncher must not call OS process APIs directly."""

from __future__ import annotations

import ast
from pathlib import Path

_FORBIDDEN_OS_ATTRS = frozenset({"kill", "killpg", "getpgid", "getpgrp"})
_FORBIDDEN_SUBPROCESS_ATTRS = frozenset({"Popen", "run"})
_FORBIDDEN_MODULE_NAMES = frozenset({"_signal_process_tree", "_process_group_exists"})


def _forbidden_calls(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                base = node.func.value.id
                attr = node.func.attr
                if base == "os" and attr in _FORBIDDEN_OS_ATTRS:
                    violations.append((node.lineno, f"os.{attr}"))
                elif base == "subprocess" and attr in _FORBIDDEN_SUBPROCESS_ATTRS:
                    violations.append((node.lineno, f"subprocess.{attr}"))
    return violations


def _forbidden_module_defs(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in _FORBIDDEN_MODULE_NAMES:
            violations.append((node.lineno, f"def {node.name}"))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in _FORBIDDEN_MODULE_NAMES:
                    violations.append((node.lineno, f"assign {target.id}"))
    return violations


def test_task_launcher_has_no_direct_process_calls() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    path = repo_root / "src/ghdag/dag/task_launcher.py"
    source = path.read_text(encoding="utf-8")
    call_violations = _forbidden_calls(source)
    def_violations = _forbidden_module_defs(source)
    assert call_violations == [], f"direct process calls: {call_violations}"
    assert def_violations == [], f"legacy process helpers: {def_violations}"
