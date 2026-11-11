"""Convention test: ghdag.__all__ resolves, matches snapshot, and no private cross-package imports."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src" / "ghdag"

_PUBLIC_API_SNAPSHOT: frozenset[str] = frozenset({
    "GhdagError",
})

_KNOWN_PRIVATE_IMPORT_VIOLATIONS: frozenset[str] = frozenset({
    "dag/recover.py",
    "pipeline/status.py",
    "llm/engines.py",
})


def private_cross_package_imports(path: Path, source: str) -> list[str]:
    """Return violation strings for private names imported from a different sub-package.

    A private name is one that starts with ``_`` but is not a dunder (``__x__``).
    The sub-package of a file is the first directory segment below ``src/ghdag``;
    root-level modules have sub-package ``""``.  Imports from the same sub-package
    are allowed.
    """
    try:
        parts = path.relative_to(_SRC).parts
    except ValueError:
        return []
    file_pkg = parts[0] if len(parts) > 1 else ""

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module or ""
        if not module.startswith("ghdag."):
            continue
        segs = module.split(".")
        import_pkg = segs[1] if len(segs) > 1 else ""
        if import_pkg == file_pkg:
            continue
        for alias in node.names or []:
            name = alias.name
            if name.startswith("_") and not (name.startswith("__") and name.endswith("__")):
                violations.append(f"line {node.lineno}: {module}.{name}")
    return violations


def _source_files() -> list[Path]:
    return sorted(_SRC.rglob("*.py"))


def test_all_public_names_resolve() -> None:
    import ghdag
    for name in ghdag.__all__:
        assert hasattr(ghdag, name), f"ghdag.__all__ contains {name!r} but getattr fails"


def test_public_api_snapshot_matches() -> None:
    import ghdag
    actual = frozenset(ghdag.__all__)
    added = actual - _PUBLIC_API_SNAPSHOT
    removed = _PUBLIC_API_SNAPSHOT - actual
    assert not added and not removed, (
        f"ghdag.__all__ diverged from snapshot; added={added}, removed={removed}. "
        "Update _PUBLIC_API_SNAPSHOT in this file when changing the public API."
    )


def test_submodule_all_names_resolve() -> None:
    import importlib
    import importlib.util
    for pyfile in _source_files():
        source = pyfile.read_text(encoding="utf-8")
        if "__all__" not in source:
            continue
        rel = str(pyfile.relative_to(_SRC))
        mod_path = pyfile.relative_to(_SRC).with_suffix("")
        mod_name = "ghdag." + ".".join(mod_path.parts)
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        if not hasattr(mod, "__all__"):
            continue
        for name in mod.__all__:
            assert hasattr(mod, name), (
                f"{rel}: __all__ contains {name!r} but getattr fails"
            )


@pytest.mark.parametrize(
    "path",
    [
        pytest.param(
            p,
            marks=pytest.mark.xfail(strict=True, reason="sumipan/nexus#3572 baseline")
            if str(p.relative_to(_SRC)) in _KNOWN_PRIVATE_IMPORT_VIOLATIONS
            else [],
        )
        for p in _source_files()
    ],
    ids=lambda p: str(p.relative_to(_SRC)),
)
def test_no_private_cross_package_imports(path: Path) -> None:
    violations = private_cross_package_imports(path, path.read_text(encoding="utf-8"))
    assert violations == [], (
        f"{path.relative_to(_SRC)}: private cross-package imports: {violations}"
    )


def test_snapshot_mismatch_is_detected() -> None:
    import ghdag
    actual = frozenset(ghdag.__all__)
    truncated = _PUBLIC_API_SNAPSHOT - {"GhdagError"}
    added = actual - truncated
    removed = truncated - actual
    assert added or removed, "Expected a diff when snapshot is missing GhdagError"


def _first_level_package_all_names() -> dict[str, list[str]]:
    packages: dict[str, list[str]] = {}
    top_init = _SRC / "__init__.py"
    if top_init.exists():
        import ghdag

        packages["ghdag"] = list(ghdag.__all__)
    for init_path in sorted(_SRC.iterdir()):
        if not init_path.is_dir():
            continue
        child_init = init_path / "__init__.py"
        if not child_init.exists():
            continue
        mod_name = "ghdag." + init_path.name
        import importlib

        mod = importlib.import_module(mod_name)
        if hasattr(mod, "__all__"):
            packages[mod_name] = list(mod.__all__)
    return packages


def _duplicate_export_names(packages: dict[str, list[str]]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for pkg, names in packages.items():
        for name in names:
            index.setdefault(name, []).append(pkg)
    return {name: pkgs for name, pkgs in index.items() if len(pkgs) > 1}


def test_first_level_package_exports_are_unique() -> None:
    duplicates = _duplicate_export_names(_first_level_package_all_names())
    assert duplicates == {}, f"Duplicate __all__ exports across packages: {duplicates}"


def test_first_level_package_export_duplicate_fixture_is_detected() -> None:
    fixture = {
        "ghdag.pipeline": ["SharedName"],
        "ghdag.workflow": ["SharedName", "Other"],
    }
    dupes = _duplicate_export_names(fixture)
    assert dupes == {"SharedName": ["ghdag.pipeline", "ghdag.workflow"]}
