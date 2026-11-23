"""Regression guards for removed latency-span audit envelope (sumipan/ghdag#7830)."""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "ghdag"
_FORBIDDEN_VOCABULARY = ("latency_span", "slack_e2e", "LATENCY_SPAN_PATH")


def test_ghdag_audit_package_is_not_importable() -> None:
    """Issue #7830 — the ``ghdag.audit`` package is no longer part of the runtime."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("ghdag.audit")


def test_runtime_source_uses_current_audit_vocabulary() -> None:
    """Issue #7830 — runtime sources must not retain removed span tracing strings."""
    hits: list[str] = []
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for token in _FORBIDDEN_VOCABULARY:
            if token in text:
                hits.append(f"{path.relative_to(_SRC_ROOT.parent.parent)}:{token}")
    assert not hits, "forbidden vocabulary in runtime source:\n" + "\n".join(hits)


def test_watchdog_is_not_a_runtime_dependency() -> None:
    """Issue #7830 — ``watchdog`` is not declared as a ghdag runtime dependency."""
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    body = pyproject.read_text(encoding="utf-8")
    project = re.search(r"(?ms)^\[project\]\s*$\n(?P<body>.*?)(?=^\[|\Z)", body)
    assert project is not None, "pyproject.toml must contain a [project] table"
    dependencies = re.search(
        r"(?ms)^dependencies\s*=\s*\[(?P<items>.*?)\]",
        project.group("body"),
    )
    assert dependencies is not None, "[project].dependencies must be declared"
    assert "watchdog" not in dependencies.group("items").lower()
