"""Convention test: deprecation warnings must name resolvable alternatives."""

from __future__ import annotations

import importlib
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src" / "ghdag"

_DEPRECATED_LINE = re.compile(r"DeprecationWarning|FutureWarning|deprecated", re.IGNORECASE)
_ALT_PATTERN = re.compile(r"ghdag(?:\.[a-zA-Z_][a-zA-Z0-9_]*)+")


def deprecation_targets(source: str) -> list[tuple[int, str | None]]:
    """Return (line_no, alternative_name) for each deprecation in source.

    ``alternative_name`` is the first ``ghdag.*`` dotted name found on the
    same line or within the next three lines; ``None`` when none is present.
    """
    lines = source.splitlines()
    results: list[tuple[int, str | None]] = []
    for i, line in enumerate(lines):
        if not _DEPRECATED_LINE.search(line):
            continue
        line_no = i + 1
        alt = _ALT_PATTERN.search(line)
        if alt:
            results.append((line_no, alt.group()))
            continue
        window = "\n".join(lines[i + 1 : i + 4])
        alt = _ALT_PATTERN.search(window)
        results.append((line_no, alt.group() if alt else None))
    return results


def unresolved(targets: list[tuple[int, str | None]]) -> list[str]:
    """Return violation strings for targets whose alternative cannot be imported."""
    violations: list[str] = []
    for line_no, name in targets:
        if name is None:
            violations.append(f"line {line_no}: no alternative named")
            continue
        parts = name.split(".")
        resolved = False
        for depth in range(len(parts), 0, -1):
            mod_name = ".".join(parts[:depth])
            try:
                obj = importlib.import_module(mod_name)
                for attr in parts[depth:]:
                    obj = getattr(obj, attr)
                resolved = True
                break
            except (ImportError, AttributeError):
                continue
        if not resolved:
            violations.append(f"line {line_no}: cannot resolve {name!r}")
    return violations


def test_no_unresolved_deprecations() -> None:
    for path in sorted(_SRC.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        targets = deprecation_targets(source)
        if not targets:
            continue
        viols = unresolved(targets)
        assert viols == [], f"{path.relative_to(_SRC)}: {viols}"


def test_deprecation_targets_detects_nonexistent_alternative() -> None:
    source = (
        'warnings.warn("x is deprecated; use ghdag.no_such_mod.Thing", DeprecationWarning)'
    )
    targets = deprecation_targets(source)
    assert len(targets) == 1
    viols = unresolved(targets)
    assert len(viols) == 1


def test_deprecation_targets_accepts_valid_alternative() -> None:
    source = (
        'warnings.warn("x is deprecated; use ghdag.quota.QuotaGate", DeprecationWarning)'
    )
    targets = deprecation_targets(source)
    assert len(targets) == 1
    viols = unresolved(targets)
    assert viols == []


def test_deprecation_targets_detects_missing_alternative() -> None:
    source = 'warnings.warn("x is deprecated", DeprecationWarning)'
    targets = deprecation_targets(source)
    assert len(targets) == 1
    viols = unresolved(targets)
    assert len(viols) == 1
