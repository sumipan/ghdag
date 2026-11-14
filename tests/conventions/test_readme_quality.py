"""README structure and inventory contracts aligned with implementation.

Refs sumipan/nexus#6454.
"""

from __future__ import annotations

import importlib
import os
import pkgutil
import re
import subprocess
import sys
from pathlib import Path

import tomllib

import ghdag
from ghdag.core.exceptions import GhdagError

from .test_cli_commands_registered import registered_commands

_REPO_ROOT = Path(__file__).resolve().parents[2]
_README = _REPO_ROOT / "README.md"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_SRC_PKG = _REPO_ROOT / "src" / "ghdag"

_CJK_RANGES = ((0x3000, 0x30FF), (0x4E00, 0x9FFF), (0xFF00, 0xFFEF))

_REQUIRED_SECTIONS = (
    "## Status",
    "## Installation",
    "## Quick Start",
    "## CLI Reference",
    "## Public API",
    "## Architecture",
    "## Configuration",
    "## Error Reference",
    "## License",
    "## Not in Scope",
    "## API Stability",
)

_ENV_GET_RE = re.compile(
    r"""os\.environ\.get\(\s*["']([A-Z][A-Z0-9_]+)["']"""
)
_ENV_LITERALS = frozenset(
    {
        "GHDAG_ENGINE_STALL_SEC",
        "GHDAG_ENGINE_STALL_INTERVAL_SEC",
    }
)

_TABLE_CELL = re.compile(r"^\|\s*`([^`]+)`\s*\|")
_CLI_CMD = re.compile(r"`ghdag\s+([^`]+)`")

_EXCEPTION_SKIP = frozenset(
    {
        "Code",
        "0",
        "SCHEMA_UNKNOWN",
        "MCP_ARGUMENT_INVALID",
        "WORKFLOWS_DIR_NOT_FOUND",
    }
)


def _readme_text() -> str:
    return _README.read_text(encoding="utf-8")


def _section(text: str, heading: str, *, stop_at: str | None = None) -> str:
    start = text.find(heading)
    assert start != -1, f"README missing {heading}"
    tail = text[start + len(heading) :]
    if stop_at:
        rel = tail.find(stop_at)
        if rel != -1:
            return tail[:rel]
    for pattern in (r"\n### ", r"\n## [^#]"):
        nxt = re.search(pattern, tail)
        if nxt:
            return tail[: nxt.start()]
    return tail


def _project_version() -> str:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    version = data.get("project", {}).get("version")
    assert isinstance(version, str) and version
    return version


def _parse_commands_table(section: str) -> set[tuple[str, ...]]:
    paths: set[tuple[str, ...]] = set()
    for line in section.splitlines():
        if not line.startswith("| `ghdag"):
            continue
        match = _CLI_CMD.search(line)
        if not match:
            continue
        parts = tuple(match.group(1).split())
        paths.add(parts)
    return paths


def _parse_env_variable_table(section: str) -> set[str]:
    keys: set[str] = set()
    in_table = False
    for line in section.splitlines():
        if line.strip() == "| Variable | Default | Read by | Meaning |":
            in_table = True
            continue
        if in_table and line.startswith("### "):
            break
        if not in_table or not line.startswith("|"):
            continue
        if "---" in line:
            continue
        match = _TABLE_CELL.match(line)
        if match and re.fullmatch(r"[A-Z][A-Z0-9_]*", match.group(1)):
            keys.add(match.group(1))
    return keys


def _parse_module_inventory(section: str) -> set[str]:
    return {m.group(1) for line in section.splitlines() if (m := _TABLE_CELL.match(line))}


def _parse_documented_exceptions(section: str) -> set[str]:
    part = section[: section.find("### Related types")]
    names: set[str] = set()
    for line in part.splitlines():
        match = _TABLE_CELL.match(line)
        if not match:
            continue
        name = match.group(1)
        if name in _EXCEPTION_SKIP:
            continue
        if re.match(r"^[A-Z][a-zA-Z0-9_]*$", name):
            names.add(name)
    return names


def _source_env_vars() -> set[str]:
    found = set(_ENV_LITERALS)
    for py in _SRC_PKG.rglob("*.py"):
        found.update(_ENV_GET_RE.findall(py.read_text(encoding="utf-8")))
    return found


def _source_module_paths() -> set[str]:
    return {
        p.relative_to(_SRC_PKG).as_posix() for p in sorted(_SRC_PKG.rglob("*.py"))
    }


def _runtime_exception_short_names() -> set[str]:
    root = Path(ghdag.__file__).resolve().parent
    prefix = ghdag.__name__ + "."
    for _, modname, _ in pkgutil.walk_packages([str(root)], prefix=prefix):
        if modname.endswith(".__main__"):
            continue
        importlib.import_module(modname)

    short_names: set[str] = set()
    for _, modname, _ in pkgutil.walk_packages([str(root)], prefix=prefix):
        if modname.endswith(".__main__"):
            continue
        mod = importlib.import_module(modname)
        for attr in dir(mod):
            obj = getattr(mod, attr)
            if (
                isinstance(obj, type)
                and issubclass(obj, BaseException)
                and obj.__module__.startswith("ghdag")
            ):
                short_names.add(obj.__name__)
    return short_names


def _runtime_exception_fqns() -> set[str]:
    root = Path(ghdag.__file__).resolve().parent
    prefix = ghdag.__name__ + "."
    for _, modname, _ in pkgutil.walk_packages([str(root)], prefix=prefix):
        if modname.endswith(".__main__"):
            continue
        importlib.import_module(modname)

    fqns: set[str] = set()
    for _, modname, _ in pkgutil.walk_packages([str(root)], prefix=prefix):
        if modname.endswith(".__main__"):
            continue
        mod = importlib.import_module(modname)
        for attr in dir(mod):
            obj = getattr(mod, attr)
            if (
                isinstance(obj, type)
                and issubclass(obj, BaseException)
                and obj.__module__.startswith("ghdag")
            ):
                fqns.add(f"{obj.__module__}.{obj.__name__}")
    return fqns


def _fenced_python_blocks(section: str) -> list[str]:
    blocks: list[str] = []
    lines = section.splitlines()
    in_fence = False
    buf: list[str] = []
    for line in lines:
        if line.strip() == "```python":
            in_fence = True
            buf = []
            continue
        if in_fence and line.strip() == "```":
            blocks.append("\n".join(buf) + ("\n" if buf else ""))
            in_fence = False
            continue
        if in_fence:
            buf.append(line)
    return blocks


def _has_cjk(text: str) -> bool:
    for ch in text:
        code = ord(ch)
        for lo, hi in _CJK_RANGES:
            if lo <= code <= hi:
                return True
    return False


def test_readme_has_required_sections_and_metadata() -> None:
    """Refs sumipan/nexus#6454 — required sections, English-only, version metadata."""
    text = _readme_text()
    assert text.startswith("# "), "README must begin with a level-1 title"
    missing = [h for h in _REQUIRED_SECTIONS if h not in text]
    assert not missing, f"missing sections: {missing}"
    assert not _has_cjk(text), "README must not contain CJK characters"

    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    project = data.get("project", {})
    version = project.get("version")
    requires = project.get("requires-python")
    license_meta = project.get("license")

    assert isinstance(version, str) and version
    assert "MIT" in str(license_meta) or license_meta == "MIT"
    assert isinstance(requires, str) and requires
    assert f"v{version}" in text
    assert requires in text
    assert "SPDX: `MIT`" in text or "SPDX: MIT" in text


def test_readme_quick_start_executes(tmp_path: Path) -> None:
    """Refs sumipan/nexus#6454 — Quick Start Python blocks run and leave expected artifacts."""
    section = _section(_readme_text(), "## Quick Start", stop_at="## CLI Reference")
    blocks = _fenced_python_blocks(section)
    assert blocks, "Quick Start should include at least one Python example"

    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_REPOSITORIES")
    }
    env["PYTHONPATH"] = str(_REPO_ROOT / "src")

    for block in blocks:
        completed = subprocess.run(
            [sys.executable, "-c", block],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, (
            f"Quick Start Python failed\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
        if "LocalGitSink" in block:
            assert "True" in completed.stdout, completed.stdout


def test_readme_cli_reference_matches_parser() -> None:
    """Refs sumipan/nexus#6454 — CLI Commands table matches argparse registration."""
    from ghdag.cli.main import _build_parser

    cli_section = _section(_readme_text(), "## CLI Reference", stop_at="## Public API")
    commands_section = _section(cli_section, "### Commands", stop_at="### Arguments")
    documented = _parse_commands_table(commands_section)
    registered = registered_commands(_build_parser())
    assert registered == documented, (
        f"missing from README: {sorted(registered - documented)}; "
        f"extra in README: {sorted(documented - registered)}"
    )


def test_readme_architecture_inventory_matches_source_tree() -> None:
    """Refs sumipan/nexus#6454 — module inventory lists every file under src/ghdag."""
    arch = _section(_readme_text(), "## Architecture", stop_at="## Configuration")
    inventory = _section(arch, "### Module inventory")
    documented = _parse_module_inventory(inventory)
    source = _source_module_paths()
    assert source == documented, (
        f"missing from README: {sorted(source - documented)}; "
        f"extra in README: {sorted(documented - source)}"
    )


def test_readme_configuration_matches_source() -> None:
    """Refs sumipan/nexus#6454 — environment variable table matches code references."""
    config = _section(_readme_text(), "## Configuration", stop_at="## Error Reference")
    env_section = _section(config, "### Environment variables")
    documented = _parse_env_variable_table(env_section)
    source = _source_env_vars()
    assert source == documented, (
        f"missing from README: {sorted(source - documented)}; "
        f"extra in README: {sorted(documented - source)}"
    )


def test_readme_error_reference_matches_exception_types() -> None:
    """Refs sumipan/nexus#6454 — Error Reference lists every public exception type."""
    err = _section(_readme_text(), "## Error Reference", stop_at="## Reviewing")
    documented_short = _parse_documented_exceptions(err)
    runtime_short = _runtime_exception_short_names()
    assert runtime_short == documented_short, (
        f"missing from README: {sorted(runtime_short - documented_short)}; "
        f"extra in README: {sorted(documented_short - runtime_short)}"
    )

    fqns = _runtime_exception_fqns()
    assert {fqn.rsplit(".", 1)[-1] for fqn in fqns} == documented_short
    short_to_fqn = {fqn.rsplit(".", 1)[-1]: fqn for fqn in fqns}
    for name in documented_short:
        fqn = short_to_fqn[name]
        module_name, class_name = fqn.rsplit(".", 1)
        cls = getattr(importlib.import_module(module_name), class_name)
        assert issubclass(cls, BaseException)
        assert issubclass(cls, GhdagError) or name in {"OwnershipError", "ConflictError"}
