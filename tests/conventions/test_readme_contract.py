"""README inventory contracts aligned with runtime (sumipan/nexus#7854)."""

from __future__ import annotations

import importlib
import os
import pkgutil
import re
import subprocess
from pathlib import Path

import tomllib

import ghdag
from ghdag.config import env as env_module
from ghdag.core.exceptions import GhdagError

_REPO_ROOT = Path(__file__).resolve().parents[2]
_README = _REPO_ROOT / "README.md"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_LOCALFORGE_MARKER = "<!-- ghdag-localforge-quick-start -->"
_FENCE_START = re.compile(r"^```bash\s*$")
_FENCE_END = re.compile(r"^```\s*$")
_TABLE_CELL = re.compile(r"^\|\s*`([^`]+)`\s*\|")
_CLI_CMD = re.compile(r"`ghdag\s+([^`]+)`")
_PACKAGE_ROW = re.compile(
    r"^\|\s*`(?P<module>ghdag(?:\.[a-z_.]+)?)`\s*\|\s*(?P<symbols>.+?)\s*\|\s*$"
)
_SYMBOL = re.compile(r"`([^`]+)`")
_EXCEPTION_ROW = re.compile(
    r"^\|\s*`([A-Za-z][A-Za-z0-9_]*)`\s*\|"
)
_ENV_OWNED_RE = re.compile(
    r"""os\.environ\.get\(\s*["']([A-Z][A-Z0-9_]+)["']"""
)
_ENV_POSITIVE_FLOAT_RE = re.compile(
    r"""_positive_float_env\(\s*["']([A-Z][A-Z0-9_]+)["']"""
)
_DEP_SPEC = re.compile(r"`([^`]+)`")


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


def _pyproject_project() -> dict[str, object]:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    project = data.get("project")
    assert isinstance(project, dict)
    return project


def _parse_installation_item(readme: str, label: str) -> str:
    for line in _section(readme, "## Installation", stop_at="## Quick Start").splitlines():
        if not line.startswith(f"| {label} |"):
            continue
        parts = [p.strip() for p in line.strip("|").split("|")]
        assert len(parts) >= 2, f"malformed Installation row for {label}"
        return parts[1]
    raise AssertionError(f"README Installation missing row: {label}")


def _dependency_tokens(cell: str) -> set[str]:
    return {spec.strip().lower() for spec in _DEP_SPEC.findall(cell)}


def _parse_public_api_table(readme: str) -> dict[str, frozenset[str]]:
    section = _section(readme, "### Package `__all__`")
    rows: dict[str, frozenset[str]] = {}
    for line in section.splitlines():
        match = _PACKAGE_ROW.match(line)
        if not match:
            continue
        module_name = match.group("module")
        package = (
            "ghdag"
            if module_name == "ghdag"
            else module_name.removeprefix("ghdag.")
        )
        symbols = frozenset(_SYMBOL.findall(match.group("symbols")))
        rows[package] = symbols
    return rows


def _runtime_public_api_exports() -> dict[str, frozenset[str]]:
    """Return non-empty ``__all__`` exports for ghdag and one-segment modules."""
    root = Path(ghdag.__file__).resolve().parent
    rows = {"ghdag": frozenset(ghdag.__all__)}
    for path in sorted(root.iterdir()):
        if path.name.startswith("_"):
            continue
        if path.is_dir() and (path / "__init__.py").is_file():
            package = path.name
        elif path.is_file() and path.suffix == ".py":
            package = path.stem
        else:
            continue
        mod = importlib.import_module(f"ghdag.{package}")
        names = getattr(mod, "__all__", None)
        if names:
            rows[package] = frozenset(names)
    return rows


def _owned_env_variable_names() -> set[str]:
    text = Path(env_module.__file__).read_text(encoding="utf-8")
    return set(_ENV_OWNED_RE.findall(text)) | set(_ENV_POSITIVE_FLOAT_RE.findall(text))


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


def _runtime_ghdag_exceptions() -> dict[str, type[BaseException]]:
    root = Path(ghdag.__file__).resolve().parent
    prefix = ghdag.__name__ + "."
    for _, modname, _ in pkgutil.walk_packages([str(root)], prefix=prefix):
        if modname.endswith(".__main__"):
            continue
        importlib.import_module(modname)

    classes: dict[str, type[BaseException]] = {}
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
                classes.setdefault(obj.__name__, obj)
    return classes


def _parse_exception_table(section: str) -> dict[str, tuple[str, int]]:
    rows: dict[str, tuple[str, int]] = {}
    in_main = False
    for line in section.splitlines():
        if line.strip().startswith("| Exception | Parent"):
            in_main = True
            continue
        if in_main and line.startswith("Two exceptions"):
            break
        if not in_main or not line.startswith("|"):
            continue
        if "---" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 5:
            continue
        name = cells[0].strip("`")
        code = cells[2].strip("`")
        exit_s = cells[3].strip("`")
        if not re.match(r"^[A-Z][a-zA-Z0-9_]*$", name):
            continue
        rows[name] = (code, int(exit_s))
    return rows


def _parse_non_ghdag_exception_table(section: str) -> frozenset[str]:
    names: set[str] = set()
    in_secondary = False
    for line in section.splitlines():
        if line.startswith("Two exceptions"):
            in_secondary = True
            continue
        if in_secondary and line.startswith("### "):
            break
        if not in_secondary or not line.startswith("|"):
            continue
        if "---" in line or "Exception | Parent" in line:
            continue
        match = _EXCEPTION_ROW.match(line)
        if match:
            names.add(match.group(1))
    return frozenset(names)


def _localforge_bash_block(readme: str) -> str:
    quick = _section(readme, "## Quick Start", stop_at="## CLI Reference")
    marker_at = quick.find(_LOCALFORGE_MARKER)
    assert marker_at != -1, "Quick Start missing localforge marker"
    tail = quick[marker_at:]
    lines = tail.splitlines()
    collecting = False
    buf: list[str] = []
    for line in lines:
        if _FENCE_START.match(line):
            collecting = True
            continue
        if collecting and _FENCE_END.match(line):
            return "\n".join(buf) + ("\n" if buf else "")
        if collecting:
            buf.append(line)
    raise AssertionError("Quick Start marker is not followed by a bash fence")


def test_readme_release_metadata_matches_pyproject() -> None:
    """Issue sumipan/nexus#7854 — Status/Installation metadata matches pyproject.toml."""
    project = _pyproject_project()
    readme = _readme_text()
    version = str(project["version"])
    requires = str(project["requires-python"])
    license_meta = project.get("license")
    runtime_deps = {d.lower() for d in project.get("dependencies", [])}  # type: ignore[arg-type]
    dev_extra = project.get("optional-dependencies", {}).get("dev", [])  # type: ignore[union-attr]
    dev_deps = {d.lower() for d in dev_extra}

    status = _section(readme, "## Status", stop_at="## Installation")
    installation = _section(readme, "## Installation", stop_at="## Quick Start")
    license_section = _section(readme, "## License")
    assert f"version-v{version}-blue" in status
    assert f"**v{version}**" in status
    assert f'version = "{version}"' in status
    assert requires in status
    assert f"@v{version}" in installation
    assert str(license_meta) in status
    assert str(license_meta) in license_section

    runtime_cell = _parse_installation_item(readme, "Runtime dependencies")
    dev_cell = _parse_installation_item(readme, "`dev` extra")
    assert _dependency_tokens(runtime_cell) == runtime_deps
    assert _dependency_tokens(dev_cell) == dev_deps
    assert "pytest-timeout" in dev_cell.lower()


def test_readme_public_api_inventory_matches_package_exports() -> None:
    """Issue sumipan/nexus#7854 — Public API table matches runtime ``__all__`` exports."""
    documented = _parse_public_api_table(_readme_text())
    runtime = _runtime_public_api_exports()
    assert documented.keys() == runtime.keys(), (
        f"readme-only packages={sorted(documented.keys() - runtime.keys())}; "
        f"runtime-only packages={sorted(runtime.keys() - documented.keys())}"
    )
    for pkg, symbols in runtime.items():
        assert documented[pkg] == symbols, (
            f"{pkg}: readme-only={documented[pkg] - symbols} "
            f"runtime-only={symbols - documented[pkg]}"
        )
        mod = ghdag if pkg == "ghdag" else importlib.import_module(f"ghdag.{pkg}")
        for symbol in symbols:
            assert hasattr(mod, symbol), f"ghdag.{pkg}.{symbol} missing"


def test_readme_environment_inventory_matches_owned_accessors() -> None:
    """Issue sumipan/nexus#7854 — env table matches ``ghdag.config.env`` accessors."""
    config = _section(_readme_text(), "## Configuration", stop_at="## Error Reference")
    documented = _parse_env_variable_table(_section(config, "### Environment variables"))
    owned = _owned_env_variable_names()
    assert documented == owned, (
        f"missing from README: {sorted(owned - documented)}; "
        f"extra in README: {sorted(documented - owned)}"
    )


def test_readme_error_inventory_matches_runtime_exception_types() -> None:
    """Issue sumipan/nexus#7854 — Error Reference matches runtime exception metadata."""
    err = _section(_readme_text(), "## Error Reference", stop_at="## Not in Scope")
    documented = _parse_exception_table(err)
    runtime = _runtime_ghdag_exceptions()
    ghdag_errors = {
        name: cls
        for name, cls in runtime.items()
        if issubclass(cls, GhdagError)
    }
    assert set(documented) == set(ghdag_errors), (
        f"missing: {sorted(set(ghdag_errors) - set(documented))}; "
        f"extra: {sorted(set(documented) - set(ghdag_errors))}"
    )
    for name, (code, exit_code) in documented.items():
        cls = ghdag_errors[name]
        assert getattr(cls, "code", None) == code, name
        assert getattr(cls, "exit_code", None) == exit_code, name
    non_ghdag_documented = _parse_non_ghdag_exception_table(err)
    non_ghdag_runtime = frozenset(
        name for name, cls in runtime.items() if not issubclass(cls, GhdagError)
    )
    assert non_ghdag_documented == non_ghdag_runtime, (
        f"non-GhdagError inventory mismatch: documented={sorted(non_ghdag_documented)} "
        f"runtime={sorted(non_ghdag_runtime)}"
    )
    for name in non_ghdag_documented:
        cls = runtime[name]
        assert not issubclass(cls, GhdagError), name
        assert cls.__module__.startswith("ghdag"), name


def test_readme_localforge_quick_start_completes(tmp_path: Path) -> None:
    """Issue sumipan/nexus#7854 — marked Quick Start bash block runs without GitHub."""
    script = _localforge_bash_block(_readme_text())
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_REPOSITORIES")
    }
    env["GHDAG_QUICKSTART_WORKDIR"] = str(tmp_path)
    env["PYTHONPATH"] = str(_REPO_ROOT / "src")
    env["PATH"] = os.environ.get("PATH", "")

    completed = subprocess.run(
        ["/bin/bash", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, (
        f"Quick Start bash failed\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    assert (tmp_path / "quickstart.out").read_text(encoding="utf-8").strip() == "quickstart-ok"
    exec_lines = [
        ln
        for ln in (tmp_path / "jobs" / "exec.jsonl").read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    assert len(exec_lines) == 1
    done_markers = list((tmp_path / "jobs" / "done").glob("*"))
    assert len(done_markers) == 1, "expected exactly one done marker"
    assert done_markers[0].read_text(encoding="utf-8").strip() == "0"
