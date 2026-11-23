"""Regression guards for removed latency-span audit envelope (sumipan/ghdag#7830)."""

from __future__ import annotations

import importlib
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
    in_dependencies = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("dependencies = ["):
            in_dependencies = True
            continue
        if in_dependencies:
            if stripped.startswith("]"):
                break
            if "watchdog" in stripped:
                pytest.fail(f"watchdog must not appear in [project].dependencies: {stripped!r}")
